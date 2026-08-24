"""Batch 16 real-hardware smoke test for the currently selected Clasq profile."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.config import AIConfig
from src.ai.hardware_detector import HardwareDetector
from src.ai.model_downloader import ModelDownloader
from src.ai.runtime_profile import ProfileSelector
from src.ai.server_manager import LlamaServerManager


def gpu_snapshot() -> dict:
    output = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,memory.free",
         "--format=csv,noheader,nounits"], text=True, timeout=5,
    ).splitlines()[0]
    name, driver, total, used, free = [part.strip() for part in output.split(",")]
    return {"gpu": name, "driver": driver, "total_mb": int(total),
            "used_mb": int(used), "free_mb": int(free)}


def main() -> int:
    cfg = AIConfig()
    hardware = HardwareDetector().detect()
    selector = ProfileSelector()
    profile = selector.select(hardware)
    report = {"before": gpu_snapshot(), "profile": profile.name if profile else None}
    if profile is None:
        report.update({"result": "NOT_SUPPORTED", "error": selector.reason})
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    downloader = ModelDownloader(profile, models_dir=Path(cfg.llama_model_path).parent)
    if not downloader.ensure_ready():
        report.update({"result": "MODEL_CACHE_FAILURE", "error": downloader.error})
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 3

    manager = LlamaServerManager(cfg, profile)
    try:
        report["cache_reused"] = True
        if not manager.ensure_running():
            report.update({"result": manager.failure_kind, "error": manager.error})
            return 4
        report["after_load"] = gpu_snapshot()
        report["readiness"] = True
        if not manager.smoke_inference():
            report.update({"result": manager.failure_kind, "error": manager.error})
            return 5
        report["after_inference"] = gpu_snapshot()
        report["inference"] = True
        report["result"] = "PASS"
        return 0
    finally:
        manager.shutdown()
        time.sleep(2)
        report["after_shutdown"] = gpu_snapshot()
        report["process_owned_after_shutdown"] = manager._proc is not None
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
