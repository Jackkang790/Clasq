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
from collections import deque
from threading import Lock, Thread
from typing import Callable, Optional

import requests

log = logging.getLogger(__name__)

_HEALTH_TIMEOUT = 3   # health check 요청당 타임아웃 (초)
_POLL_INTERVAL  = 2   # readiness 폴링 간격 (초)
_POPEN_TYPE = subprocess.Popen


class LlamaServerManager:

    def __init__(self, config=None, profile=None, *, http=None,
                 popen_factory=None, monotonic=None, sleep=None, job_factory=None):
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
        self.failure_kind: Optional[str] = None         # machine-readable startup/inference cause
        self._http = http or requests
        # Keep None as "use module function now" so existing monkeypatch-based
        # tests remain valid; explicit injections are still deterministic seams.
        self._popen = popen_factory
        self._monotonic = monotonic
        self._sleep = sleep
        self._job_factory = job_factory
        self._job = None
        self._stderr_thread: Optional[Thread] = None
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._shutting_down = False
        self._recovery_attempts = 0
        self._recovery_lock = Lock()
        self.max_runtime_recoveries = 1

    # ── 공개 API ────────────────────────────────────────────────────────

    def ensure_running(self) -> bool:
        """서버가 준비 상태인지 확인하고, 필요하면 시작한다.

        LLAMA_MANAGED=false 이면 외부 서버 상태만 확인하고 시작하지 않는다.
        항상 bool 을 반환하며 예외를 던지지 않는다.
        """
        self.error = None
        self.failure_kind = None
        if self._shutting_down:
            self.failure_kind = "app_shutting_down"
            self.error = "앱 종료 중에는 로컬 AI 서버를 시작하지 않습니다."
            return False

        if not self._cfg.llama_managed:
            self.is_available = self._check_health()
            if not self.is_available:
                self.failure_kind = "readiness_failure"
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
            self.failure_kind = "server_start_failure"
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
        self._shutting_down = True
        log.info("llama-server shutdown requested owned_pid=%s", getattr(self._proc, "pid", None))
        self._cleanup_process()

    def _cleanup_process(self):
        """Stop the owned process and release its pipes and Job handle."""
        if self._proc is None:
            if self._job is not None:
                self._job.close()
                self._job = None
            self.is_available = False
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
            if self._proc.stderr:
                try:
                    self._proc.stderr.close()
                except Exception:
                    pass
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=1)
                self._stderr_thread = None
            self._proc = None
            if self._job is not None:
                self._job.close()
                self._job = None
            self.is_available = False

    def recover_if_needed(self) -> bool:
        """Perform at most one same-profile runtime recovery per manager session."""
        with self._recovery_lock:
            if self._shutting_down:
                self.failure_kind = "app_shutting_down"
                return False
            if self._proc is not None and self._proc.poll() is None and self._check_health():
                return True
            self.failure_kind = "runtime_crash"
            log.warning(
                "llama-server runtime crash detected recovery_attempt=%d max_recoveries=%d",
                self._recovery_attempts + 1, self.max_runtime_recoveries,
            )
            self.is_available = False
            self._cleanup_process()
            if self._recovery_attempts >= self.max_runtime_recoveries:
                self.error = "로컬 AI 서버가 종료되어 복구할 수 없습니다."
                log.error("llama-server runtime recovery exhausted")
                return False
            self._recovery_attempts += 1
            self._shutting_down = False
            recovered = self._start()
            log.log(logging.INFO if recovered else logging.ERROR,
                    "llama-server runtime recovery result=%s", recovered)
            return recovered

    def smoke_inference(self) -> bool:
        """Run a short OpenAI-compatible inference and validate its shape."""
        self.failure_kind = None
        try:
            response = self._http.post(
                self._cfg.chat_completions_url,
                json={
                    "model": self._cfg.model,
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
                timeout=self._cfg.timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty response content")
            log.info("AI startup smoke inference completed response_chars=%d", len(content.strip()))
            return True
        except requests.exceptions.Timeout as exc:
            self.failure_kind = "inference_timeout"
            self.error = "로컬 AI 응답 시간이 초과되었습니다."
            log.warning("%s: %s", self.error, exc)
            return False
        except Exception as exc:
            self.failure_kind = "inference_failure"
            self.error = f"AI inference smoke test failed: {exc}"
            log.error(self.error)
            return False

    # ── 내부 메서드 ─────────────────────────────────────────────────────

    @property
    def _health_url(self) -> str:
        return f"http://{self._cfg.llama_host}:{self._cfg.llama_port}/health"

    def _check_health(self) -> bool:
        """GET /health 가 200 이면 True."""
        try:
            r = self._http.get(self._health_url, timeout=_HEALTH_TIMEOUT)
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
                self.failure_kind = (
                    "model_missing" if label != "llama-server.exe" else "server_start_failure"
                )
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
        profile_name = getattr(self._profile, "name", None) or "config"
        log.info(
            "llama-server start requested profile=%s host=%s port=%d gpu_layers=%d context=%d",
            profile_name, self._cfg.llama_host, self._cfg.llama_port, ngl, ctx,
        )

        # Windows에서 콘솔 창이 팝업되지 않도록 설정
        creation_flags = 0
        startupinfo = None
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        try:
            self._proc = (self._popen or subprocess.Popen)(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,       # 조기 종료 시 오류 메시지 수집용
                creationflags=creation_flags,
                startupinfo=startupinfo,
            )
            log.info("llama-server process started pid=%d profile=%s", self._proc.pid, profile_name)
            if os.name == "nt" and (
                self._job_factory is not None or isinstance(self._proc, _POPEN_TYPE)
            ):
                if self._job_factory is None:
                    from src.ai.windows_job import KillOnCloseJob
                    self._job = KillOnCloseJob()
                else:
                    self._job = self._job_factory()
                self._job.assign(self._proc)
            if self._popen is not None or isinstance(self._proc, _POPEN_TYPE):
                self._start_stderr_reader()
        except OSError as exc:
            self.error = f"llama-server 실행 실패: {exc}"
            self.failure_kind = "server_start_failure"
            log.error(self.error)
            self._cleanup_process()
            return False

        # readiness 폴링
        monotonic = self._monotonic or time.monotonic
        sleep = self._sleep or time.sleep
        deadline = monotonic() + self._cfg.llama_startup_timeout
        while monotonic() < deadline and not self._shutting_down:
            # 프로세스가 예상치 않게 종료된 경우
            if self._proc.poll() is not None:
                if self._stderr_thread is not None:
                    self._stderr_thread.join(timeout=0.2)
                stderr_tail = "\n".join(self._stderr_tail)[-4000:]
                self.error = (
                    f"llama-server 가 시작 직후 종료되었습니다 "
                    f"(exit={self._proc.returncode})."
                    + (f" 오류: {stderr_tail}" if stderr_tail else "")
                )
                self.failure_kind = self._classify_start_failure(stderr_tail)
                log.error(self.error)
                self._cleanup_process()
                return False

            if self._check_health():
                log.info(
                    "llama-server ready (pid=%d, host=%s, port=%d)",
                    self._proc.pid, self._cfg.llama_host, self._cfg.llama_port,
                )
                self.is_available = True
                return True

            sleep(_POLL_INTERVAL)

        if self._shutting_down:
            self.failure_kind = "app_shutting_down"
            self.error = "앱 종료로 로컬 AI 시작을 취소했습니다."
            self._cleanup_process()
            return False

        self.error = (
            f"llama-server 가 {self._cfg.llama_startup_timeout}초 내에 "
            "응답하지 않습니다. 모델 로딩 중이거나 VRAM 이 부족할 수 있습니다."
        )
        self.failure_kind = "readiness_failure"
        log.error(
            "llama-server readiness timeout pid=%s timeout_seconds=%s",
            getattr(self._proc, "pid", None), self._cfg.llama_startup_timeout,
        )
        self.shutdown()
        return False

    def _start_stderr_reader(self) -> None:
        pipe = self._proc.stderr if self._proc else None
        if pipe is None:
            return
        def drain():
            try:
                for raw in iter(pipe.readline, b""):
                    self._stderr_tail.append(raw.decode("utf-8", errors="replace").rstrip())
            except (OSError, ValueError):
                pass
        self._stderr_thread = Thread(target=drain, name="llama-server-stderr", daemon=True)
        self._stderr_thread.start()

    @staticmethod
    def _classify_start_failure(stderr: str) -> str:
        text = stderr.lower()
        gpu_markers = ("cuda out of memory", "cuda error out of memory", "vram", "failed to allocate cuda")
        ram_markers = ("std::bad_alloc", "cannot allocate memory", "not enough memory")
        if any(marker in text for marker in gpu_markers):
            return "cuda_oom"
        if any(marker in text for marker in ram_markers):
            return "ram_oom"
        return "server_start_failure"
