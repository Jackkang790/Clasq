"""Measure Qwen document-analysis throughput without DB or file side effects."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ai import QwenClient
from src.utils.file_pipeline import FileAnalyzer


TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".xml", ".yaml", ".yml", ".csv", ".html", ".htm"}
EXCLUDED_DIRECTORIES = {".git", ".idea", "node_modules", ".venv", "venv", "__pycache__"}


@dataclass
class RequestResult:
    file_path: str
    latency_sec: float
    success: bool
    timeout: bool
    error: str = ""


class GPUMonitor:
    def __init__(self, interval_sec: float = 1.0):
        self.interval_sec = interval_sec
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_sec + 2)

    def _run(self):
        while not self._stop.is_set():
            try:
                completed = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                )
                timestamp = time.time()
                for line in completed.stdout.splitlines():
                    fields = [field.strip() for field in line.split(",")]
                    if len(fields) == 5:
                        self.samples.append({
                            "timestamp": timestamp,
                            "index": int(fields[0]),
                            "name": fields[1],
                            "memory_used_mib": int(fields[2]),
                            "memory_total_mib": int(fields[3]),
                            "utilization_gpu_percent": int(fields[4]),
                        })
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
            self._stop.wait(self.interval_sec)

    def summary(self) -> list[dict]:
        summaries = []
        for gpu_index in sorted({sample["index"] for sample in self.samples}):
            samples = [sample for sample in self.samples if sample["index"] == gpu_index]
            summaries.append({
                "index": gpu_index,
                "name": samples[0]["name"],
                "max_memory_used_mib": max(sample["memory_used_mib"] for sample in samples),
                "average_utilization_gpu_percent": round(
                    statistics.fmean(sample["utilization_gpu_percent"] for sample in samples), 2
                ),
                "max_utilization_gpu_percent": max(
                    sample["utilization_gpu_percent"] for sample in samples
                ),
                "samples": len(samples),
            })
        return summaries


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[rank]


def find_samples(root: Path, count: int) -> list[Path]:
    samples = []
    for current_root, directories, names in os.walk(root, topdown=True, followlinks=False):
        directories[:] = [
            name for name in directories if name.casefold() not in EXCLUDED_DIRECTORIES
        ]
        for name in sorted(names, key=str.casefold):
            path = Path(current_root) / name
            if path.suffix.lower() in TEXT_EXTENSIONS and path.is_file():
                samples.append(path.resolve())
                if len(samples) == count:
                    return samples
    return samples


def load_workloads(paths: list[Path], max_chars: int) -> list[tuple[str, str]]:
    workloads = []
    for path in paths:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")[:max_chars]
        workloads.append((str(path), text))
    return workloads


def analyze_one(workload: tuple[str, str]) -> RequestResult:
    file_path, text = workload
    analyzer = FileAnalyzer(client=QwenClient())
    started = time.perf_counter()
    try:
        result = analyzer.analyze_document_text(file_path, text)
        latency = time.perf_counter() - started
        success = result.get("status") == "SUCCESS" and not result.get("error")
        error = "" if success else str(result.get("error") or "analysis failed")
        lowered = error.casefold()
        return RequestResult(
            file_path, latency, success,
            "timeout" in lowered or "시간" in lowered or "초과" in lowered,
            error,
        )
    except Exception as exc:  # benchmark must retain all per-request failures
        latency = time.perf_counter() - started
        error = str(exc)
        lowered = error.casefold()
        return RequestResult(
            file_path, latency, False,
            "timeout" in lowered or "시간" in lowered or "초과" in lowered,
            error,
        )


def run_benchmark(workloads: list[tuple[str, str]], concurrency: int) -> dict:
    gpu_monitor = GPUMonitor()
    gpu_monitor.start()
    started = time.perf_counter()
    results = []
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(analyze_one, workload) for workload in workloads]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(
                    f"[REQUEST] concurrency={concurrency} "
                    f"file={Path(result.file_path).name} latency={result.latency_sec:.3f} "
                    f"success={result.success}",
                    flush=True,
                )
    finally:
        total_time = time.perf_counter() - started
        gpu_monitor.stop()

    latencies = [result.latency_sec for result in results]
    success_count = sum(result.success for result in results)
    failure_count = len(results) - success_count
    timeout_count = sum(result.timeout for result in results)
    summary = {
        "files": len(workloads),
        "concurrency": concurrency,
        "total_time_sec": round(total_time, 3),
        "throughput_files_per_sec": round(len(workloads) / total_time, 4),
        "average_latency_sec": round(statistics.fmean(latencies), 3),
        "p50_latency_sec": round(percentile(latencies, 0.50), 3),
        "p95_latency_sec": round(percentile(latencies, 0.95), 3),
        "success_count": success_count,
        "failure_count": failure_count,
        "timeout_count": timeout_count,
        "gpu": gpu_monitor.summary(),
        "requests": [asdict(result) for result in results],
    }
    print("[QWEN BENCH]")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path.home() / "Downloads"))
    parser.add_argument("--files", type=int, default=20)
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--output", default="benchmarks/qwen_concurrency_results.json")
    args = parser.parse_args()

    client = QwenClient()
    models = client.list_models()
    print(f"[SERVER] base_url={client.config.base_url} model={client.config.model}")
    print(f"[SERVER] models={json.dumps(models, ensure_ascii=False)}")

    paths = find_samples(Path(args.root), args.files)
    if len(paths) != args.files:
        raise SystemExit(f"Only {len(paths)} text samples found; {args.files} required")
    workloads = load_workloads(paths, args.max_chars)
    print("[SAMPLES]")
    for path, text in workloads:
        print(f"{path} chars={len(text)}")

    summaries = []
    for concurrency in (1, 2, 4):
        summary = run_benchmark(workloads, concurrency)
        summaries.append(summary)
        errors = " ".join(
            request["error"].casefold() for request in summary["requests"] if request["error"]
        )
        if concurrency == 4 and ("out of memory" in errors or "oom" in errors):
            print("[STOP] OOM detected at concurrency=4; no higher concurrency will be tested.")
            break

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"samples": [path for path, _ in workloads], "results": summaries},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[RESULT] {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
