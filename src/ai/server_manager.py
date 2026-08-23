"""llama-server 프로세스 생명주기 관리.

규칙:
- 이 앱이 직접 시작한 프로세스(self._proc)만 종료한다.
- 외부에서 이미 실행 중인 llama-server는 건드리지 않는다.
- 공개 메서드는 예외를 던지지 않는다. 오류는 self.error 에 저장된다.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

_HEALTH_TIMEOUT = 3   # health check 요청당 타임아웃 (초)
_POLL_INTERVAL  = 2   # readiness 폴링 간격 (초)


class LlamaServerManager:

    def __init__(self, config=None, profile=None):
        """
        config  : AIConfig 인스턴스 (None이면 기본값 사용)
        profile : RuntimeProfile 인스턴스 (None이면 config 경로 사용)
                  profile이 주어지면 모델 경로와 실행 옵션을 profile에서 읽는다.
                  llama-server.exe 경로는 항상 config에서 읽는다.
        """
        from src.ai.config import AIConfig
        self._cfg = config or AIConfig()
        self._profile = profile
        self._proc: Optional[subprocess.Popen] = None  # 앱이 시작한 프로세스만
        self.error: Optional[str] = None               # 마지막 오류 메시지
        self.is_available: bool = False                # 서버 사용 가능 여부

    # ── 공개 API ────────────────────────────────────────────────────────

    def ensure_running(self) -> bool:
        """서버가 준비 상태인지 확인하고, 필요하면 시작한다.

        LLAMA_MANAGED=false 이면 외부 서버 상태만 확인하고 시작하지 않는다.
        항상 bool 을 반환하며 예외를 던지지 않는다.
        """
        self.error = None

        if not self._cfg.llama_managed:
            self.is_available = self._check_health()
            if not self.is_available:
                self.error = (
                    f"AI 서버가 응답하지 않습니다 ({self._health_url}). "
                    "LLAMA_MANAGED=false 이므로 자동 시작하지 않습니다."
                )
            return self.is_available

        # 이미 응답 중이면 그대로 사용 (외부 서버 포함)
        if self._check_health():
            log.info("llama-server already responding at %s", self._health_url)
            self.is_available = True
            return True

        # 포트가 사용 중인데 응답이 없으면 다른 프로세스로 판단
        conflict = self._detect_port_conflict()
        if conflict:
            self.error = conflict
            self.is_available = False
            return False

        # 서버 시작
        return self._start()

    def is_running(self) -> bool:
        """현재 서버가 health check 에 응답하는지 확인한다."""
        return self._check_health()

    def shutdown(self):
        """앱이 직접 시작한 프로세스만 종료한다.

        외부에서 실행 중이던 llama-server 는 건드리지 않는다.
        """
        if self._proc is None:
            return
        pid = self._proc.pid
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
            log.info("llama-server stopped (pid=%d)", pid)
        except subprocess.TimeoutExpired:
            log.warning("llama-server did not stop in time, killing (pid=%d)", pid)
            try:
                self._proc.kill()
                self._proc.wait(timeout=3)
            except Exception as exc:
                log.error("kill failed (pid=%d): %s", pid, exc)
        except Exception as exc:
            log.error("shutdown error (pid=%d): %s", pid, exc)
        finally:
            self._proc = None
            self.is_available = False

    # ── 내부 메서드 ─────────────────────────────────────────────────────

    @property
    def _health_url(self) -> str:
        return f"http://{self._cfg.llama_host}:{self._cfg.llama_port}/health"

    def _check_health(self) -> bool:
        """GET /health 가 200 이면 True."""
        try:
            r = requests.get(self._health_url, timeout=_HEALTH_TIMEOUT)
            return r.status_code == 200
        except Exception:
            return False

    def _detect_port_conflict(self) -> Optional[str]:
        """포트가 사용 중이지만 llama-server 가 아닌 경우 오류 메시지를 반환."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            in_use = s.connect_ex((self._cfg.llama_host, self._cfg.llama_port)) == 0
        if not in_use:
            return None  # 포트 비어 있음, 정상
        return (
            f"포트 {self._cfg.llama_port} 가 이미 사용 중이지만 "
            "llama-server 가 응답하지 않습니다. "
            "다른 프로세스가 해당 포트를 점유하고 있을 수 있습니다."
        )

    def _resolve_paths(self):
        """profile 또는 config 에서 모델 경로와 실행 옵션을 결정한다."""
        exe = self._cfg.llama_server_exe  # exe는 항상 config에서

        if self._profile is not None:
            # profile 기준 — models_dir 는 config.llama_model_path 의 디렉터리
            models_dir = os.path.dirname(self._cfg.llama_model_path)
            model      = os.path.join(models_dir, self._profile.model_filename)
            mmproj     = os.path.join(models_dir, self._profile.mmproj_filename)
            ngl        = self._profile.n_gpu_layers
            ctx        = self._profile.context_size
            extra      = list(self._profile.extra_args)
        else:
            # 기존 config 기준 (하위 호환)
            model  = self._cfg.llama_model_path
            mmproj = self._cfg.llama_mmproj_path
            ngl    = self._cfg.llama_n_gpu_layers
            ctx    = self._cfg.llama_context_size
            extra  = ["--log-disable"]

        return exe, model, mmproj, ngl, ctx, extra

    def _start(self) -> bool:
        """llama-server 를 실행하고 readiness 를 기다린다."""
        exe, model, mmproj, ngl, ctx, extra = self._resolve_paths()

        # 파일 존재 확인
        for label, path in [("llama-server.exe", exe),
                             ("모델 파일", model),
                             ("mmproj 파일", mmproj)]:
            if not os.path.isfile(path):
                self.error = f"{label}를 찾을 수 없습니다: {path}"
                log.error(self.error)
                return False

        # 검증된 실행 명령 구성
        cmd = [
            exe,
            "-m",       model,
            "--mmproj", mmproj,
            "--host",   self._cfg.llama_host,
            "--port",   str(self._cfg.llama_port),
            "-ngl",     str(ngl),
            "-c",       str(ctx),
            *extra,
        ]
        log.info("Starting llama-server: %s", " ".join(cmd))

        # Windows에서 콘솔 창이 팝업되지 않도록 설정
        creation_flags = 0
        startupinfo = None
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,       # 조기 종료 시 오류 메시지 수집용
                creationflags=creation_flags,
                startupinfo=startupinfo,
            )
        except OSError as exc:
            self.error = f"llama-server 실행 실패: {exc}"
            log.error(self.error)
            return False

        # readiness 폴링
        deadline = time.monotonic() + self._cfg.llama_startup_timeout
        while time.monotonic() < deadline:
            # 프로세스가 예상치 않게 종료된 경우
            if self._proc.poll() is not None:
                stderr_tail = ""
                try:
                    raw = self._proc.stderr.read(1000) if self._proc.stderr else b""
                    stderr_tail = raw.decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
                self.error = (
                    f"llama-server 가 시작 직후 종료되었습니다 "
                    f"(exit={self._proc.returncode})."
                    + (f" 오류: {stderr_tail}" if stderr_tail else "")
                )
                log.error(self.error)
                self._proc = None
                return False

            if self._check_health():
                log.info(
                    "llama-server ready (pid=%d, host=%s, port=%d)",
                    self._proc.pid, self._cfg.llama_host, self._cfg.llama_port,
                )
                self.is_available = True
                return True

            time.sleep(_POLL_INTERVAL)

        self.error = (
            f"llama-server 가 {self._cfg.llama_startup_timeout}초 내에 "
            "응답하지 않습니다. 모델 로딩 중이거나 VRAM 이 부족할 수 있습니다."
        )
        log.error(self.error)
        return False
