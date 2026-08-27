"""백그라운드 AI 환경 초기화 워커 (llama_server 모드 전용).

메인 UI 스레드를 블로킹하지 않고 다음 작업을 순서대로 처리한다.
  1. 시스템 확인 (HardwareDetector)
  2. 프로필 선택 (ProfileSelector)
  3. 저장공간 확인
  4. 모델 파일 확인 / 다운로드 / 검증 (ModelDownloader)
  5. llama-server 시작 + readiness (LlamaServerManager)

완료 시 ready(True, ""), 실패 시 ready(False, "오류메시지") signal 발생.
"""
from __future__ import annotations

import logging
import os
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 시작 단계 정의
# ---------------------------------------------------------------------------

class StartupPhase(Enum):
    SYSTEM_CHECK   = auto()
    PROFILE_SELECT = auto()
    STORAGE_CHECK  = auto()
    MODEL_CHECK    = auto()
    MODEL_DOWNLOAD = auto()
    MODEL_VERIFY   = auto()
    SERVER_START   = auto()
    MODEL_LOADING  = auto()
    READY          = auto()
    ERROR          = auto()


_PHASE_LABEL: dict[StartupPhase, str] = {
    StartupPhase.SYSTEM_CHECK:   "시스템 확인 중",
    StartupPhase.PROFILE_SELECT: "AI 실행 환경 확인 중",
    StartupPhase.STORAGE_CHECK:  "저장공간 확인 중",
    StartupPhase.MODEL_CHECK:    "모델 확인 중",
    StartupPhase.MODEL_DOWNLOAD: "모델 다운로드 중",
    StartupPhase.MODEL_VERIFY:   "모델 검증 중",
    StartupPhase.SERVER_START:   "로컬 AI 시작 중",
    StartupPhase.MODEL_LOADING:  "AI 모델 로딩 중",
    StartupPhase.READY:          "준비 완료",
    StartupPhase.ERROR:          "오류 발생",
}


# ---------------------------------------------------------------------------
# StartupWorker
# ---------------------------------------------------------------------------

class StartupWorker(QThread):
    """AI 환경 초기화를 백그라운드에서 처리하는 QThread."""

    # (phase, 사용자에게 표시할 문구)
    phase_changed = Signal(object, str)
    # (파일명, 받은 바이트, 전체 바이트) — 다운로드 진행률
    # PySide's ``int`` maps to a signed 32-bit Qt int. Model artifacts exceed
    # that range, so preserve Python integers across the worker/UI boundary.
    progress_changed = Signal(str, object, object)
    # (성공 여부, 오류 메시지)
    ready = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server_manager = None  # 완료 후 MainWindow가 읽어감
        self.selected_profile = None
        self.fallback_occurred = False
        self.attempted_profiles: list[str] = []
        self.failure_kind: Optional[str] = None
        self._downloader = None

    def cancel(self) -> None:
        self.requestInterruption()
        if self._downloader is not None:
            self._downloader.cancel()

    @property
    def server_manager(self):
        """ready(True) signal 수신 후 MainWindow가 가져갈 서버 매니저."""
        return self._server_manager

    # ── 내부 헬퍼 ───────────────────────────────────────────────────────

    def _emit(self, phase: StartupPhase, extra: str = "") -> None:
        label = _PHASE_LABEL.get(phase, phase.name)
        if extra:
            label = f"{label} — {extra}"
        self.phase_changed.emit(phase, label)

    # ── 메인 흐름 ────────────────────────────────────────────────────────

    def run(self) -> None:
        from src.ai.config import AIConfig
        from src.ai.hardware_detector import HardwareDetector
        from src.ai.model_downloader import ModelDownloader
        from src.ai.runtime_profile import ProfileSelector
        from src.ai.server_manager import LlamaServerManager

        cfg = AIConfig()

        # ── 1. 시스템 확인 ────────────────────────────────────────────────
        self._emit(StartupPhase.SYSTEM_CHECK)
        hw = HardwareDetector().detect()
        log.info("HW: %s  VRAM=%dMB  RAM=%dMB",
                 hw.gpu_name, hw.gpu_vram_mb, hw.system_ram_mb)

        # ── 2. 프로필 선택 ────────────────────────────────────────────────
        self._emit(StartupPhase.PROFILE_SELECT)
        sel = ProfileSelector()
        profiles = sel.select_candidates(hw)

        if not profiles:
            log.warning("프로필 없음: %s", sel.reason)
            self.ready.emit(False, sel.reason)
            return

        profile = profiles[0]
        log.info(
            "AI profile selected primary=%s candidates=%s gpu_model=%s total_vram_mb=%d free_vram_mb=%d",
            profile.name, ",".join(item.name for item in profiles), hw.gpu_name,
            hw.gpu_vram_mb, hw.gpu_vram_free_mb,
        )

        # ── 3. 저장공간 확인 ──────────────────────────────────────────────
        self._emit(StartupPhase.STORAGE_CHECK)
        models_dir = Path(cfg.llama_model_path).parent
        models_dir.mkdir(parents=True, exist_ok=True)

        # Space is checked per missing artifact by ModelDownloader.  A fully
        # validated cache must remain usable even when free disk space is low.

        # ── 4. 모델 파일 확인 / 다운로드 / 검증 ──────────────────────────
        self._emit(StartupPhase.MODEL_CHECK)

        def _on_dl_progress(filename: str, received: int, total: int) -> None:
            self.progress_changed.emit(filename, received, total)
            safe_received = max(0, min(received, total)) if total else max(0, received)
            if total and received > total:
                log.warning("UI received invalid model progress downloaded_bytes=%d total_bytes=%d", received, total)
            pct = int(safe_received / total * 100) if total else 0
            recv_gb = safe_received / 1024 ** 3
            tot_gb  = total  / 1024 ** 3
            self._emit(
                StartupPhase.MODEL_DOWNLOAD,
                f"{pct}%  {recv_gb:.1f}GB / {tot_gb:.1f}GB",
            )

        def _on_dl_status(msg: str) -> None:
            if "검증" in msg or "verify" in msg.lower():
                self._emit(StartupPhase.MODEL_VERIFY, msg)
            elif "다운로드" in msg or "download" in msg.lower():
                self._emit(StartupPhase.MODEL_DOWNLOAD, msg)
            else:
                self._emit(StartupPhase.MODEL_CHECK, msg)

        downloader = ModelDownloader(
            profile=profile,
            models_dir=models_dir,
            on_progress=_on_dl_progress,
            on_status=_on_dl_status,
        )
        self._downloader = downloader
        model_ok = downloader.ensure_ready()

        if self.isInterruptionRequested():
            self.ready.emit(False, "모델 준비가 취소되었습니다.")
            return
        if not model_ok:
            msg = downloader.error or "모델 파일 준비 실패"
            log.error(msg)
            self.ready.emit(False, msg)
            return

        # ── 5. llama-server 시작 + readiness + inference ─────────────────
        last_error = "AI 서버 시작 실패"
        for index, candidate in enumerate(profiles):
            self.attempted_profiles.append(candidate.name)
            self._emit(StartupPhase.SERVER_START, candidate.name)
            manager = LlamaServerManager(cfg, profile=candidate)
            # Preserve the exact snapshot used for profile selection so
            # diagnostics do not depend on a second, possibly transient query.
            manager.hardware_info = hw
            self._server_manager = manager

            if not manager.is_running():
                self._emit(StartupPhase.MODEL_LOADING, "최대 2분 소요될 수 있습니다")

            if manager.ensure_running() and manager.smoke_inference():
                self.selected_profile = candidate
                self.fallback_occurred = index > 0
                self.failure_kind = None
                self._emit(StartupPhase.READY)
                log.info("AI startup ready profile=%s fallback=%s", candidate.name, index > 0)
                self.ready.emit(True, "")
                return

            last_error = manager.error or last_error
            self.failure_kind = manager.failure_kind
            log.warning(
                "AI profile attempt failed profile=%s kind=%s fallback_remaining=%d",
                candidate.name, manager.failure_kind, len(profiles) - index - 1,
            )
            manager.shutdown()
            # Artifact/config errors are not VRAM fallback candidates.
            if manager.failure_kind in {"model_missing", "ram_oom"}:
                break

        log.error(last_error)
        self.ready.emit(False, last_error)
