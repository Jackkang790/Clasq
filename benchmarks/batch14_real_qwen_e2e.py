"""Batch 14 real-Qwen E2E. Requires the product GGUF files and NVIDIA GPU."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ai.config import AIConfig
from src.ai.hardware_detector import HardwareDetector
from src.ai.model_downloader import ModelDownloader
from src.ai.qwen_client import QwenClient
from src.ai.runtime_profile import ProfileSelector
from src.ai.server_manager import LlamaServerManager
from src.utils.core import ClasqCore
from src.utils.workers import (
    FolderAnalysisPlanWorker,
    FolderScanAndTagWorker,
    OrganizeApplyWorker,
    OrganizeUndoWorker,
)


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_worker(worker, signal_name: str = "completed"):
    completed, errors = [], []
    getattr(worker, signal_name).connect(completed.append)
    worker.error.connect(errors.append)
    worker.run()
    if errors:
        raise RuntimeError(errors[0])
    if not completed:
        raise RuntimeError(f"{type(worker).__name__} produced no result")
    return completed[0]


def gpu_sample() -> dict:
    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    output = subprocess.check_output(command, text=True, timeout=10).strip()
    name, total, used, utilization = [part.strip() for part in output.split(",")]
    return {
        "name": name,
        "total_mib": int(total),
        "used_mib": int(used),
        "utilization_percent": int(utilization),
    }


def main() -> int:
    started = time.perf_counter()
    cfg = AIConfig()
    hardware = HardwareDetector().detect()
    selector = ProfileSelector()
    profile = selector.select(hardware)
    if profile is None:
        raise RuntimeError(selector.reason)

    cache_started = time.perf_counter()
    downloader = ModelDownloader(profile, models_dir=Path(cfg.llama_model_path).parent)
    if not downloader.ensure_ready():
        raise RuntimeError(downloader.error)
    cache_check_seconds = time.perf_counter() - cache_started

    manager = LlamaServerManager(cfg, profile)
    report: dict = {
        "model": profile.model_filename,
        "mmproj": profile.mmproj_filename,
        "quantization": "Q4_K_M",
        "llama_server": cfg.llama_server_exe,
        "cache_check_seconds": cache_check_seconds,
        "gpu_before": gpu_sample(),
    }

    try:
        server_started = time.perf_counter()
        if not manager.ensure_running():
            raise RuntimeError(manager.error)
        report["server_ready_seconds"] = time.perf_counter() - server_started
        report["health"] = requests.get(
            f"http://{cfg.llama_host}:{cfg.llama_port}/health", timeout=5
        ).json()
        report["gpu_loaded"] = gpu_sample()

        client = QwenClient(cfg)
        inference_started = time.perf_counter()
        raw = client.request_text(
            "Return only JSON: {\"status\":\"ok\",\"tags\":[\"Clasq\",\"Qwen\"]}",
            temperature=0.0,
            max_tokens=80,
            timeout=cfg.timeout,
        )
        parsed = client.parse_json_content(raw)
        report["direct_inference_seconds"] = time.perf_counter() - inference_started
        report["direct_response"] = parsed

        with tempfile.TemporaryDirectory(prefix="clasq_batch14_", ignore_cleanup_errors=True) as temp:
            root = Path(temp)
            inbox = root / "inbox"
            inbox.mkdir()
            original = inbox / "quarterly_report.txt"
            original.write_text(
                "Clasq Batch 14 quarterly finance report. Revenue increased and the "
                "engineering team completed the local AI file organization milestone.",
                encoding="utf-8",
            )
            untouched = inbox / "outside_plan.bin"
            untouched.write_bytes(b"must remain unchanged")
            image_path = inbox / "clasq_chart.png"
            image = Image.new("RGB", (640, 360), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((80, 220, 180, 320), fill="royalblue")
            draw.rectangle((240, 150, 340, 320), fill="seagreen")
            draw.rectangle((400, 70, 500, 320), fill="orange")
            draw.text((80, 25), "Clasq quarterly growth chart", fill="black")
            image.save(image_path)
            original_hash = sha256(str(original))
            db_path = str(root / "clasq_e2e.db")
            core = ClasqCore(db_path=db_path)

            initial_plan = run_worker(FolderAnalysisPlanWorker([str(inbox)], db_path=db_path))
            analysis_started = time.perf_counter()
            background_summary = run_worker(
                FolderScanAndTagWorker([str(original)], core), signal_name="finished"
            )
            report["file_inference_seconds"] = time.perf_counter() - analysis_started
            if background_summary.get("success") != 1:
                raise RuntimeError(f"real background analysis failed: {background_summary}")

            image_started = time.perf_counter()
            image_analysis = core.process_file_upload(str(image_path))
            report["image_inference_seconds"] = time.perf_counter() - image_started
            if image_analysis.get("status") != "SUCCESS":
                raise RuntimeError(f"real image analysis failed: {image_analysis.get('error')}")

            plan = run_worker(FolderAnalysisPlanWorker([str(inbox)], db_path=db_path))
            registered = core.get_files_for_organize()
            tags = registered[0].get("tags") if registered else []
            if not tags:
                raise RuntimeError("real Qwen response produced no persisted tags")
            groups = core.group_files_by_tags(registered)
            preview = core.build_organize_preview(groups, str(inbox))
            item = next(p for p in preview if os.path.normcase(p["source_path"]) == os.path.normcase(str(original)))
            if item["has_conflict"]:
                raise RuntimeError("unexpected preview conflict")

            db_file = next(f for f in registered if os.path.normcase(f["file_path"]) == os.path.normcase(str(original)))
            apply_plan = [{
                "file_id": db_file["id"],
                "file_name": db_file["file_name"],
                "file_path": str(original),
                "target_path": item["target_path"],
            }]
            apply_result = run_worker(OrganizeApplyWorker(apply_plan, db_path))
            if len(apply_result.get("moved", [])) != 1 or not apply_result.get("operation_id"):
                raise RuntimeError(f"apply failed: {apply_result}")
            moved = item["target_path"]

            connection = sqlite3.connect(db_path)
            apply_paths = {
                "files": connection.execute("SELECT file_path FROM files WHERE id=?", (db_file["id"],)).fetchone()[0],
                "text_index": connection.execute("SELECT file_path FROM file_text_index WHERE file_path=?", (moved,)).fetchone()[0],
                "fingerprint": connection.execute("SELECT file_path FROM file_fingerprint_cache WHERE file_path=?", (moved,)).fetchone()[0],
            }
            extracted_before = connection.execute(
                "SELECT extracted_text FROM file_text_index WHERE file_path=?", (moved,)
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT id, operation_id, original_path, moved_path, file_hash, file_size, status "
                "FROM organize_history WHERE operation_id=?",
                (apply_result["operation_id"],),
            ).fetchall()
            connection.close()
            records = [{
                "id": row[0], "operation_id": row[1], "original_path": row[2],
                "moved_path": row[3], "file_hash": row[4], "file_size": row[5],
                "status": row[6],
            } for row in rows]

            undo_result = run_worker(OrganizeUndoWorker(records, db_path))
            if len(undo_result.get("undone", [])) != 1:
                raise RuntimeError(f"undo failed: {undo_result}")

            connection = sqlite3.connect(db_path)
            undo_paths = {
                "files": connection.execute("SELECT file_path FROM files WHERE id=?", (db_file["id"],)).fetchone()[0],
                "text_index": connection.execute("SELECT file_path FROM file_text_index WHERE file_path=?", (str(original),)).fetchone()[0],
                "fingerprint": connection.execute("SELECT file_path FROM file_fingerprint_cache WHERE file_path=?", (str(original),)).fetchone()[0],
            }
            extracted_after = connection.execute(
                "SELECT extracted_text FROM file_text_index WHERE file_path=?", (str(original),)
            ).fetchone()[0]
            history_status = connection.execute(
                "SELECT status FROM organize_history WHERE operation_id=?",
                (apply_result["operation_id"],),
            ).fetchone()[0]
            schema_version = connection.execute(
                "SELECT MAX(version) FROM db_schema_version"
            ).fetchone()[0]
            connection.close()

            report["e2e"] = {
                "analysis_status": "SUCCESS",
                "initial_pending": initial_plan["counts"].get("pending", 0),
                "background_summary": background_summary,
                "image_status": image_analysis["status"],
                "image_tags": image_analysis.get("metadata", {}).get("tags", []),
                "tags": tags,
                "plan_scanned": plan["counts"]["scanned"],
                "preview_count": len(preview),
                "auto_apply": False,
                "apply_paths": apply_paths,
                "undo_paths": undo_paths,
                "history_status": history_status,
                "hash_preserved": sha256(str(original)) == original_hash,
                "content_preserved": original.is_file(),
                "moved_removed": not Path(moved).exists(),
                "plan_outside_file_preserved": (
                    untouched.read_bytes() == b"must remain unchanged" and image_path.is_file()
                ),
                "extracted_text_preserved": extracted_before == extracted_after,
                "schema_version": schema_version,
            }
    finally:
        pid = manager._proc.pid if manager._proc is not None else None
        manager.shutdown()
        report["server_pid"] = pid
        report["health_after_shutdown"] = manager.is_running()
        report["total_seconds"] = time.perf_counter() - started

    restart = LlamaServerManager(cfg, profile)
    restart_started = time.perf_counter()
    try:
        report["restart_ready"] = restart.ensure_running()
        report["restart_ready_seconds"] = time.perf_counter() - restart_started
        if not report["restart_ready"]:
            raise RuntimeError(restart.error)
    finally:
        restart.shutdown()
        report["restart_health_after_shutdown"] = restart.is_running()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
