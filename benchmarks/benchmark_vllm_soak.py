"""Long-running vLLM soak test using the read-only Clasq analysis path."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path

from benchmark_product_backends import run_batch, select_samples, set_backend


def gpu_snapshot(label: str, started: float) -> dict:
    snapshot = {"label": label, "elapsed_sec": round(time.perf_counter() - started, 3)}
    try:
        query = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, check=True, timeout=5,
        )
        used, free, util, temperature, power = [x.strip() for x in query.stdout.split(",")[:5]]
        snapshot.update({
            "memory_used_mib": int(used),
            "memory_free_mib": int(free),
            "utilization_percent": int(util),
            "temperature_c": int(temperature),
            "power_w": float(power),
        })
    except Exception as exc:
        snapshot["gpu_error"] = f"{type(exc).__name__}: {exc}"
    try:
        processes = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        values = []
        for line in processes.stdout.splitlines():
            value = line.strip()
            if value.isdigit():
                values.append(int(value))
        snapshot["process_gpu_memory_mib"] = max(values) if values else None
    except Exception:
        snapshot["process_gpu_memory_mib"] = None
    return snapshot


def write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\Users\USER1\Downloads")
    parser.add_argument("--hours", type=float, default=3.0)
    parser.add_argument("--idle-seconds", type=int, default=300)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8202/v1")
    parser.add_argument("--model", default="qwen3-vl-8b-vllm-4bit")
    args = parser.parse_args()

    set_backend(args.base_url, args.model)
    paths = select_samples(args.root)
    started = time.perf_counter()
    deadline = started + args.hours * 3600
    report = {
        "requested_hours": args.hours,
        "idle_seconds": args.idle_seconds,
        "sample_count": len(paths),
        "distribution": {kind: sum(Path(path).suffix.lower() in suffixes for path in paths) for kind, suffixes in {
            "text": {".txt", ".md", ".markdown"}, "docx": {".docx"}, "pdf": {".pdf"},
            "pptx": {".pptx"}, "image": {".png", ".jpg", ".jpeg"},
            "video": {".mp4", ".mkv", ".avi"},
        }.items()},
        "started_at_epoch": time.time(),
        "gpu_snapshots": [],
        "batches": [],
        "stopped_reason": "",
    }
    output = Path(args.output)
    report["gpu_snapshots"].append(gpu_snapshot("server_ready", started))
    write_report(output, report)

    batch_number = 0
    while time.perf_counter() < deadline:
        batch_number += 1
        report["gpu_snapshots"].append(gpu_snapshot(f"batch_{batch_number}_start", started))
        summary, _ = run_batch(paths, concurrency=2, monitor_gpu=True)
        summary["batch_number"] = batch_number
        summary["elapsed_end_sec"] = round(time.perf_counter() - started, 3)
        report["batches"].append(summary)
        report["gpu_snapshots"].append(gpu_snapshot(f"batch_{batch_number}_end", started))
        write_report(output, report)

        fatal_errors = " ".join(summary.get("error_types", {})).casefold()
        if "out of memory" in fatal_errors or "cuda" in fatal_errors:
            report["stopped_reason"] = "oom_or_cuda_error"
            break

        idle_started = time.perf_counter()
        one_minute_recorded = False
        while time.perf_counter() - idle_started < args.idle_seconds:
            elapsed_idle = time.perf_counter() - idle_started
            if not one_minute_recorded and elapsed_idle >= 60:
                report["gpu_snapshots"].append(gpu_snapshot(f"batch_{batch_number}_idle_1m", started))
                one_minute_recorded = True
                write_report(output, report)
            time.sleep(min(10, max(0.1, args.idle_seconds - elapsed_idle)))
        report["gpu_snapshots"].append(gpu_snapshot(f"batch_{batch_number}_idle_5m", started))
        write_report(output, report)

    report["finished_at_epoch"] = time.time()
    report["total_elapsed_sec"] = round(time.perf_counter() - started, 3)
    report["total_files_processed"] = sum(batch["files"] for batch in report["batches"])
    report["total_success"] = sum(batch["success_count"] for batch in report["batches"])
    report["total_failure"] = sum(batch["failure_count"] for batch in report["batches"])
    report["total_timeout"] = sum(batch["timeout_count"] for batch in report["batches"])
    report["gpu_snapshots"].append(gpu_snapshot("final_idle", started))
    throughputs = [batch["files_per_sec"] for batch in report["batches"]]
    if throughputs:
        report["throughput"] = {
            "first": throughputs[0], "last": throughputs[-1],
            "mean": round(statistics.fmean(throughputs), 4),
            "change_percent": round((throughputs[-1] / throughputs[0] - 1) * 100, 2),
        }
    write_report(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 1 if report["stopped_reason"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
