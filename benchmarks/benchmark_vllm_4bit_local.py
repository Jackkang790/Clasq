"""Read-only local vLLM benchmark. No DB writes or file moves."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai import ImageAnalyzer, QwenClient
from src.utils.file_pipeline import FileAnalyzer


class GPUMonitor:
    def __init__(self, interval: float = 0.2) -> None:
        self.interval = interval
        self.samples: list[dict] = []
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> dict:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        if not self.samples:
            return {}
        return {
            "peak_memory_mib": max(x["memory_mib"] for x in self.samples),
            "average_gpu_utilization_percent": round(
                statistics.fmean(x["utilization"] for x in self.samples), 2
            ),
            "max_gpu_utilization_percent": max(x["utilization"] for x in self.samples),
            "samples": len(self.samples),
        }

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                memory, utilization = result.stdout.strip().split(",")[:2]
                self.samples.append({
                    "at": time.time(),
                    "memory_mib": int(memory.strip()),
                    "utilization": int(utilization.strip()),
                })
            except Exception:
                pass
            self.stop_event.wait(self.interval)


def measure(name: str, function) -> dict:
    monitor = GPUMonitor()
    monitor.start()
    started = time.perf_counter()
    try:
        output = function()
        error = ""
        success = True
    except Exception as exc:
        output = None
        error = f"{type(exc).__name__}: {exc}"
        success = False
    latency = time.perf_counter() - started
    gpu = monitor.stop()
    result = {
        "name": name,
        "latency_sec": round(latency, 3),
        "success": success,
        "error": error,
        "gpu": gpu,
        "output": output,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def analyze_document(file_path: str, text: str) -> dict:
    return FileAnalyzer(client=QwenClient()).analyze_document_text(file_path, text)


def run_many(name: str, file_path: str, text: str, count: int, concurrency: int) -> dict:
    monitor = GPUMonitor()
    monitor.start()
    started = time.perf_counter()
    requests = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        submitted = [
            (time.perf_counter(), executor.submit(analyze_document, file_path, text))
            for _ in range(count)
        ]
        for request_started, future in submitted:
            try:
                output = future.result()
                success = output.get("status") == "SUCCESS" and not output.get("error")
                error = "" if success else str(output.get("error") or "analysis failed")
            except Exception as exc:
                output = None
                success = False
                error = f"{type(exc).__name__}: {exc}"
            requests.append({
                "latency_sec": round(time.perf_counter() - request_started, 3),
                "success": success,
                "error": error,
                "output": output,
            })
    total = time.perf_counter() - started
    gpu = monitor.stop()
    result = {
        "name": name,
        "files": count,
        "concurrency": concurrency,
        "total_time_sec": round(total, 3),
        "throughput_files_per_sec": round(count / total, 4),
        "average_latency_sec": round(
            statistics.fmean(x["latency_sec"] for x in requests), 3
        ),
        "success_count": sum(x["success"] for x in requests),
        "failure_count": sum(not x["success"] for x in requests),
        "timeout_count": sum("timeout" in x["error"].casefold() for x in requests),
        "gpu": gpu,
        "requests": requests,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=r"C:\Users\USER1\Downloads\Add.png")
    parser.add_argument(
        "--document", default=r"C:\Users\USER1\Downloads\bigdata-worksheet (1).html"
    )
    parser.add_argument("--output", default=os.path.join(os.environ.get("TEMP", "."), "clasq_vllm_4bit_results.json"))
    parser.add_argument("--concurrency-only", action="store_true")
    args = parser.parse_args()

    document_path = Path(args.document)
    text = document_path.read_bytes().decode("utf-8", errors="replace")[:12000]
    results = {
        "server": {
            "base_url": QwenClient().config.base_url,
            "model": QwenClient().config.model,
            "models": QwenClient().list_models(),
        },
        "inputs": {
            "image": str(Path(args.image)),
            "document": str(document_path),
            "document_chars": len(text),
            "short_prompt": "대한민국의 수도를 한 문장으로 답하세요.",
        },
        "measurements": [],
    }

    if not args.concurrency_only:
        results["measurements"].append(measure(
            "short_text",
            lambda: QwenClient().request_text(
                results["inputs"]["short_prompt"], max_tokens=50, temperature=0
            ),
        ))
        results["measurements"].append(measure(
            "add_png_ocr",
            lambda: ImageAnalyzer().extract_ocr(args.image),
        ))
        results["measurements"].append(measure(
            "add_png_two_pass",
            lambda: ImageAnalyzer().analyze_image(args.image),
        ))
        results["measurements"].append(measure(
            "document_12k",
            lambda: analyze_document(str(document_path), text),
        ))
    results["measurements"].append(run_many(
        "document_12k_c1_two", str(document_path), text, 2, 1
    ))
    results["measurements"].append(run_many(
        "document_12k_c2_two", str(document_path), text, 2, 2
    ))
    results["measurements"].append(run_many(
        "document_12k_c2_four", str(document_path), text, 4, 2
    ))

    Path(args.output).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"RESULT_FILE={args.output}")
    return 0 if all(x.get("failure_count", 0) == 0 and x.get("success", True) for x in results["measurements"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
