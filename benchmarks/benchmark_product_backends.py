"""Compare Clasq analysis backends without DB writes or file moves.

Raw paths, extracted text, and model outputs remain in memory. The JSON report
contains only aggregate metrics and anonymized identifiers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai import ImageAnalyzer
from src.ai import video_analyzer as video_analyzer_module
from src.utils.core import scan_directory_files
from src.utils.file_pipeline import FileAnalyzer, TextExtractor
from src.utils.main_processor import MainProcessor
from src.utils.query_parser import SearchQueryParser


TARGET_COUNTS = {
    "text": 10,
    "docx": 8,
    "pdf": 8,
    "pptx": 8,
    "image": 14,
    "video": 2,
}

# On this Windows host Defender can briefly hold the emptied FFmpeg frame
# directory open. Product inference has already completed at that point, but
# TemporaryDirectory cleanup raises WinError 5 and masks the valid result.
# Suppress cleanup-only errors in this benchmark; no product module is edited.
_temporary_directory = tempfile.TemporaryDirectory
video_analyzer_module.tempfile.TemporaryDirectory = lambda: _temporary_directory(
    ignore_cleanup_errors=True
)


def anonymous_id(path: str) -> str:
    return hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode()).hexdigest()[:12]


def file_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return "text"
    if suffix == ".docx":
        return "docx"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".pptx":
        return "pptx"
    if suffix in {".png", ".jpg", ".jpeg"}:
        return "image"
    if suffix in {".mp4", ".mkv", ".avi"}:
        return "video"
    return None


def select_samples(root: str) -> list[str]:
    buckets: dict[str, list[Path]] = {key: [] for key in TARGET_COUNTS}
    for raw in scan_directory_files(root):
        path = Path(raw)
        kind = file_kind(path)
        if kind is None:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if kind != "video" and not (100 <= size <= 50 * 1024 * 1024):
            continue
        buckets[kind].append(path)

    selected: list[Path] = []
    for kind, count in TARGET_COUNTS.items():
        candidates = buckets[kind]
        if kind == "video":
            candidates.sort(key=lambda p: p.stat().st_size)
        else:
            candidates.sort(key=lambda p: hashlib.sha256(
                os.path.normcase(str(p)).encode()
            ).digest())
        if len(candidates) < count:
            raise RuntimeError(f"not enough {kind} samples: {len(candidates)} < {count}")
        selected.extend(candidates[:count])
    return [str(path) for path in selected]


def set_backend(base_url: str, model: str) -> None:
    os.environ["AI_BASE_URL"] = base_url
    os.environ["AI_MODEL"] = model
    os.environ["AI_TIMEOUT"] = "300"
    os.environ["VIDEO_AI_TIMEOUT"] = "900"


def new_processor() -> MainProcessor:
    analyzer = FileAnalyzer()
    return MainProcessor(
        TextExtractor(), analyzer, SearchQueryParser(client=analyzer.client),
        initialize_registry=False,
    )


def read_gpu() -> dict:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, check=True, timeout=5,
        )
        used, free, util = [int(value.strip()) for value in proc.stdout.split(",")[:3]]
        return {"memory_used_mib": used, "memory_free_mib": free, "utilization": util}
    except Exception:
        return {}


class GPUMonitor:
    def __init__(self) -> None:
        self.samples: list[dict] = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> dict:
        self.stop_event.set()
        self.thread.join(timeout=3)
        if not self.samples:
            return {}
        return {
            "peak_memory_mib": max(x["memory_used_mib"] for x in self.samples),
            "mean_utilization_percent": round(statistics.fmean(x["utilization"] for x in self.samples), 2),
            "max_utilization_percent": max(x["utilization"] for x in self.samples),
            "final": self.samples[-1],
        }

    def _run(self) -> None:
        while not self.stop_event.is_set():
            sample = read_gpu()
            if sample:
                sample["time"] = time.time()
                self.samples.append(sample)
            self.stop_event.wait(0.5)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return ordered[index]


def analyze_one(path: str, local_state: threading.local) -> dict:
    processor = getattr(local_state, "processor", None)
    if processor is None:
        processor = new_processor()
        local_state.processor = processor
    started = time.perf_counter()
    try:
        result = processor.analyze_file(path).metadata_result
        latency = time.perf_counter() - started
        success = result.get("status") == "SUCCESS" and not result.get("error")
        error = "" if success else str(result.get("error") or result.get("message") or "failed")
        metadata = result.get("metadata", {}) if success else {}
    except Exception as exc:
        latency = time.perf_counter() - started
        success, metadata = False, {}
        error = f"{type(exc).__name__}: {exc}"
    return {
        "id": anonymous_id(path),
        "kind": file_kind(Path(path)),
        "latency": latency,
        "success": success,
        "timeout": "timeout" in error.casefold(),
        "error_type": error.split(":", 1)[0][:80] if error else "",
        "metadata": metadata,
    }


def run_batch(paths: list[str], concurrency: int, monitor_gpu: bool) -> tuple[dict, dict[str, dict]]:
    monitor = GPUMonitor() if monitor_gpu else None
    if monitor:
        monitor.start()
    local_state = threading.local()
    started = time.perf_counter()
    results: list[dict] = []
    checkpoints: dict[str, dict] = {"start": read_gpu()} if monitor_gpu else {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(analyze_one, path, local_state) for path in paths]
        for future in as_completed(futures):
            results.append(future.result())
            completed = len(results)
            if monitor_gpu and completed in {10, 25, 50}:
                checkpoints[str(completed)] = read_gpu()
            print(f"completed={completed}/{len(paths)}", flush=True)
    total = time.perf_counter() - started
    gpu = monitor.stop() if monitor else {}
    latencies = [item["latency"] for item in results]
    success = sum(item["success"] for item in results)
    summary = {
        "files": len(results),
        "concurrency": concurrency,
        "total_sec": round(total, 3),
        "files_per_sec": round(len(results) / total, 4),
        "mean_latency_sec": round(statistics.fmean(latencies), 3),
        "median_latency_sec": round(statistics.median(latencies), 3),
        "p95_latency_sec": round(percentile(latencies, 0.95), 3),
        "min_latency_sec": round(min(latencies), 3),
        "max_latency_sec": round(max(latencies), 3),
        "success_count": success,
        "failure_count": len(results) - success,
        "timeout_count": sum(item["timeout"] for item in results),
        "error_types": dict(Counter(item["error_type"] for item in results if item["error_type"])),
        "gpu": gpu,
        "gpu_checkpoints": checkpoints,
        "by_kind": {},
    }
    for kind in TARGET_COUNTS:
        subset = [item for item in results if item["kind"] == kind]
        summary["by_kind"][kind] = {
            "files": len(subset),
            "success": sum(item["success"] for item in subset),
            "mean_latency_sec": round(statistics.fmean(item["latency"] for item in subset), 3),
        }
    return summary, {item["id"]: item for item in results}


def normalized(value) -> str:
    return " ".join(str(value or "").casefold().split())


def text_similarity(left, right) -> float:
    return round(SequenceMatcher(None, normalized(left), normalized(right)).ratio(), 3)


def compare_quality(remote: dict[str, dict], local: dict[str, dict], limit: int = 10) -> dict:
    comparisons = []
    common = [key for key in remote if key in local and remote[key]["success"] and local[key]["success"]]
    selected = []
    quality_quotas = {"text": 2, "docx": 2, "pdf": 2, "pptx": 1, "image": 2, "video": 1}
    for kind, quota in quality_quotas.items():
        selected.extend(sorted(key for key in common if remote[key]["kind"] == kind)[:quota])
    for key in selected[:limit]:
        a, b = remote[key]["metadata"], local[key]["metadata"]
        tags_a = {normalized(x) for x in a.get("tags", []) if normalized(x)}
        tags_b = {normalized(x) for x in b.get("tags", []) if normalized(x)}
        union = tags_a | tags_b
        comparisons.append({
            "id": key,
            "kind": remote[key]["kind"],
            "ocr_similarity": text_similarity(a.get("ocr_text"), b.get("ocr_text")),
            "display_name_similarity": text_similarity(a.get("display_name"), b.get("display_name")),
            "tag_jaccard": round(len(tags_a & tags_b) / len(union), 3) if union else 1.0,
            "category_equal": normalized(a.get("category")) == normalized(b.get("category")),
            "subcategory_equal": normalized(a.get("sub_category")) == normalized(b.get("sub_category")),
            "description_similarity": text_similarity(
                a.get("description") or a.get("summary"),
                b.get("description") or b.get("summary"),
            ),
            "confidence_delta": round(abs(float(a.get("confidence", 0) or 0) - float(b.get("confidence", 0) or 0)), 3),
            "required_fields_present": all(
                bool(a.get(field)) and bool(b.get(field))
                for field in ("display_name", "tags", "description")
            ),
        })
    return {
        "files": len(comparisons),
        "items": comparisons,
        "mean_ocr_similarity": round(statistics.fmean(x["ocr_similarity"] for x in comparisons), 3),
        "mean_display_name_similarity": round(statistics.fmean(x["display_name_similarity"] for x in comparisons), 3),
        "mean_tag_jaccard": round(statistics.fmean(x["tag_jaccard"] for x in comparisons), 3),
        "category_agreement_rate": round(statistics.fmean(x["category_equal"] for x in comparisons), 3),
        "mean_description_similarity": round(statistics.fmean(x["description_similarity"] for x in comparisons), 3),
        "missing_required_fields": sum(not x["required_fields_present"] for x in comparisons),
    }


def run_add_three(image: str) -> dict:
    measurements = []
    for label in ("first", "warm_1", "warm_2"):
        started = time.perf_counter()
        result = ImageAnalyzer().analyze_image(image)
        latency = time.perf_counter() - started
        metadata = result.get("metadata", {})
        measurements.append({
            "label": label,
            "latency_sec": round(latency, 3),
            "success": result.get("status") == "SUCCESS" and not result.get("error"),
            "ocr_sha256": hashlib.sha256(normalized(metadata.get("ocr_text")).encode()).hexdigest(),
            "ocr_expected": normalized(metadata.get("ocr_text")) == "+ 환자 등록",
            "required_fields_present": all(metadata.get(x) for x in ("display_name", "tags", "description")),
            "category_present": bool(metadata.get("category")),
        })
    return {"runs": measurements}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\Users\USER1\Downloads")
    parser.add_argument("--mode", choices=("compare", "local-throughput"), required=True)
    parser.add_argument("--remote-url", default="http://127.0.0.1:8100/v1")
    parser.add_argument("--remote-model", default="qwen3-vl-8b")
    parser.add_argument("--local-url", default="http://127.0.0.1:8202/v1")
    parser.add_argument("--local-model", default="qwen3-vl-8b-vllm-4bit")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    paths = select_samples(args.root)
    report = {
        "sample_count": len(paths),
        "distribution": dict(Counter(file_kind(Path(path)) for path in paths)),
        "mode": args.mode,
    }
    if args.mode == "compare":
        add_path = str(Path(args.root) / "Add.png")
        set_backend(args.remote_url, args.remote_model)
        report["remote_add"] = run_add_three(add_path)
        report["remote"], remote_results = run_batch(paths, 2, False)
        set_backend(args.local_url, args.local_model)
        report["local_add"] = run_add_three(add_path)
        report["local_c2"], local_results = run_batch(paths, 2, True)
        report["quality"] = compare_quality(remote_results, local_results)
    else:
        set_backend(args.local_url, args.local_model)
        report["local_c1"], _ = run_batch(paths, 1, True)
        report["local_c2"], _ = run_batch(paths, 2, True)

    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
