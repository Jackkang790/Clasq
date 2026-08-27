"""모델 파일 다운로드 및 무결성 관리.

역할:
- RuntimeProfile 에서 필요한 파일 목록을 받아 준비한다.
- GPU/하드웨어를 직접 판단하지 않는다.
- 파일이 이미 정상 상태이면 다운로드하지 않는다.

SHA-256 정책:
- 최초 다운로드 직후: 전체 SHA-256 계산 후 manifest 저장
- 이후 실행: 파일 크기 + mtime 비교 (빠름)
- 파일 변경 감지 시: 전체 SHA-256 재검증
- 검증 실패 시: 파일 삭제 후 재다운로드

파일 위치:
  {models_dir}/
    qwen3vl-8b-q4_k_m.gguf
    mmproj-bf16.gguf
    .clasq_manifest.json    ← SHA-256 / mtime 캐시
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

import requests

from .runtime_profile import RuntimeProfile

log = logging.getLogger(__name__)

# 타입: (파일명, 받은 바이트, 전체 바이트) → None
ProgressCallback = Callable[[str, int, int], None]

_CHUNK_SIZE      = 1024 * 1024   # 1 MB
_CONNECT_TIMEOUT = 15
_READ_TIMEOUT    = 60
_MAX_ATTEMPTS = 3
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class DownloadCancelled(Exception):
    pass


class ModelDownloader:
    """선택된 RuntimeProfile 에 필요한 모델 파일을 준비한다."""

    def __init__(
        self,
        profile: RuntimeProfile,
        models_dir: Optional[Path] = None,
        on_progress: Optional[ProgressCallback] = None,
        on_status: Optional[Callable[[str], None]] = None,
        request_get=None,
        cancel_event=None,
        max_attempts: int = _MAX_ATTEMPTS,
    ):
        self._profile = profile
        self._models_dir = Path(models_dir) if models_dir else _default_models_dir()
        self._on_progress = on_progress
        self._on_status = on_status
        self._request_get = request_get or requests.get
        self._cancel_event = cancel_event or threading.Event()
        self._max_attempts = max(1, int(max_attempts))
        self._manifest_path = self._models_dir / ".clasq_manifest.json"
        self.error: Optional[str] = None

    def cancel(self) -> None:
        self._cancel_event.set()

    def cache_state(self) -> dict[str, str]:
        state = {}
        for role, filename, expected_sha256, expected_size in (
            ("main", self._profile.model_filename, self._profile.model_sha256, self._profile.model_size_bytes),
            ("mmproj", self._profile.mmproj_filename, self._profile.mmproj_sha256, self._profile.mmproj_size_bytes),
        ):
            path = self._models_dir / filename
            if not path.exists():
                value = "missing"
            elif path.stat().st_size != expected_size:
                value = "invalid_size"
            elif self._is_valid_cached(path, expected_sha256, expected_size):
                value = "valid"
            else:
                value = "needs_validation"
            state[role] = value
        return state

    # ── 공개 API ────────────────────────────────────────────────────────

    def ensure_ready(self) -> bool:
        """모델 파일이 준비 상태인지 확인하고, 필요하면 다운로드한다.

        항상 bool 을 반환한다. 예외를 던지지 않는다.
        """
        self.error = None
        try:
            self._models_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.error = "모델 캐시 폴더를 준비하지 못했습니다."
            return False

        files = [
            (self._profile.model_filename,  self._profile.model_sha256,  self._profile.model_size_bytes,  self._profile.model_url),
            (self._profile.mmproj_filename, self._profile.mmproj_sha256, self._profile.mmproj_size_bytes, self._profile.mmproj_url),
        ]

        for filename, expected_sha256, expected_size, url in files:
            ok = self._ensure_file(filename, expected_sha256, expected_size, url)
            if not ok:
                return False
        return True

    @property
    def models_dir(self) -> Path:
        return self._models_dir

    def model_path(self) -> Path:
        return self._models_dir / self._profile.model_filename

    def mmproj_path(self) -> Path:
        return self._models_dir / self._profile.mmproj_filename

    # ── 파일 단위 준비 ─────────────────────────────────────────────────

    def _ensure_file(
        self,
        filename: str,
        expected_sha256: str,
        expected_size: int,
        url: str,
    ) -> bool:
        allowed = {self._profile.model_filename, self._profile.mmproj_filename}
        if Path(filename).name != filename or filename not in allowed:
            self.error = "허용되지 않은 모델 파일 이름입니다."
            return False
        target = self._models_dir / filename
        self._emit_status(f"{filename} 확인 중...")

        if target.exists() and target.stat().st_size != expected_size:
            log.warning("%s: invalid cached file size; downloading again", self._role(filename))
            target.unlink(missing_ok=True)
            self._remove_manifest(filename)

        if target.exists():
            if self._is_valid_cached(target, expected_sha256, expected_size):
                log.info("%s: 기존 파일 정상 (캐시)", filename)
                return True
            # 캐시 불일치 → 전체 SHA-256 재검증
            self._emit_status(f"{filename} 무결성 검증 중...")
            if self._verify_sha256(target, expected_sha256):
                self._save_manifest(filename, target, expected_sha256)
                log.info("%s: 기존 파일 SHA-256 검증 통과", filename)
                return True
            log.warning("%s: SHA-256 불일치 — 재다운로드", filename)
            target.unlink(missing_ok=True)
            self._remove_manifest(filename)

        # 저장공간 확인
        free_bytes = _free_space_bytes(self._models_dir)
        needed = expected_size + 512 * 1024 * 1024  # +512MB 여유
        if free_bytes < needed:
            gb_needed = needed / 1024 ** 3
            gb_free   = free_bytes / 1024 ** 3
            self.error = (
                f"저장공간이 부족합니다. "
                f"필요: {gb_needed:.1f} GB  여유: {gb_free:.1f} GB"
            )
            log.error(self.error)
            return False

        return self._download(filename, url, expected_sha256, expected_size, target)

    def _download(
        self,
        filename: str,
        url: str,
        expected_sha256: str,
        expected_size: int,
        target: Path,
    ) -> bool:
        lock_path = target.with_name(target.name + ".download.lock")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            self.error = f"{self._role(filename)} 모델 다운로드가 이미 진행 중입니다."
            return False
        try:
            return self._download_locked(filename, url, expected_sha256, expected_size, target)
        finally:
            lock_path.unlink(missing_ok=True)

    def _download_locked(self, filename, url, expected_sha256, expected_size, target) -> bool:
        tmp_path = target.with_name(target.name + ".part")
        role = self._role(filename)
        if not _is_https_url(url):
            self.error = f"{role} 모델 다운로드는 HTTPS만 허용됩니다."
            return False
        existing_size = tmp_path.stat().st_size if tmp_path.exists() else 0
        if existing_size > expected_size:
            log.warning("partial exceeds expected size role=%s partial_bytes=%d expected_bytes=%d; resetting",
                        role, existing_size, expected_size)
            self._cleanup_partial(tmp_path)
            existing_size = 0
        log.info("model download started role=%s model_url=%s expected_bytes=%d existing_partial_bytes=%d resume=%s",
                 role, _safe_url(url), expected_size, existing_size, bool(existing_size))
        self._emit_status(f"{filename} 다운로드 중...")
        for attempt in range(1, self._max_attempts + 1):
            if self._cancel_event.is_set():
                self.error = "모델 다운로드가 취소되었습니다."
                return False
            try:
                existing_size = tmp_path.stat().st_size if tmp_path.exists() else 0
                request_headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}
                response = self._request_get(url, stream=True, timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                                             headers=request_headers)
                if response.status_code == 416 and existing_size:
                    log.warning("range rejected role=%s partial_bytes=%d; resetting", role, existing_size)
                    self._cleanup_partial(tmp_path)
                    existing_size = 0
                    request_headers = {}
                    response = self._request_get(url, stream=True,
                                                 timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT), headers={})
                response.raise_for_status()
                if not _is_https_url(getattr(response, "url", url)):
                    raise ValueError("insecure redirect")
                content_length = _header_int(response.headers, "content-length")
                content_range_text = response.headers.get("content-range", "")
                content_range = _parse_content_range(content_range_text)
                log.info("model response role=%s status=%s final_url=%s content_length=%s content_range=%s "
                         "accept_ranges=%s range_requested=%s existing_partial_bytes=%d",
                         role, response.status_code, _safe_url(getattr(response, "url", url)),
                         content_length, content_range_text or None, response.headers.get("accept-ranges"),
                         bool(request_headers), existing_size)
                if existing_size and response.status_code == 206:
                    if not content_range or content_range[0] != existing_size:
                        raise ValueError("unexpected content range")
                    total = content_range[2]
                    mode = "ab"
                elif existing_size and response.status_code == 200:
                    log.warning("server ignored range role=%s; restarting from byte zero", role)
                    existing_size = 0
                    total = content_length or expected_size
                    mode = "wb"
                else:
                    existing_size = 0
                    total = content_range[2] if content_range else (content_length or expected_size)
                    mode = "wb"
                if total != expected_size:
                    log.warning("remote total differs from expected role=%s remote_total_bytes=%d expected_bytes=%d",
                                role, total, expected_size)
                    raise ValueError("remote size mismatch")
                received = existing_size
                sha = hashlib.sha256()
                if existing_size:
                    with open(tmp_path, "rb") as partial:
                        while chunk := partial.read(_CHUNK_SIZE):
                            sha.update(chunk)
                if self._on_progress:
                    self._on_progress(filename, received, total)
                with open(tmp_path, mode) as output:
                    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                        if self._cancel_event.is_set():
                            raise DownloadCancelled
                        if not chunk:
                            continue
                        output.write(chunk)
                        sha.update(chunk)
                        received += len(chunk)
                        if received > total:
                            log.warning("downloaded exceeds total role=%s downloaded_bytes=%d remote_total_bytes=%d",
                                        role, received, total)
                            raise ValueError("download exceeds remote size")
                        if self._on_progress:
                            self._on_progress(filename, received, total)
                final_size = tmp_path.stat().st_size if tmp_path.exists() else 0
                log.info("model stream ended role=%s downloaded_bytes=%d remote_total_bytes=%d final_local_bytes=%d",
                         role, received, total, final_size)
                if final_size <= 0 or received != total or final_size != expected_size:
                    raise ValueError("size mismatch")
                if sha.hexdigest() != expected_sha256.lower():
                    raise ValueError("hash mismatch")
                os.replace(tmp_path, target)
                self._save_manifest(filename, target, expected_sha256)
                log.info("model download validated role=%s bytes=%d", role, received)
                return True
            except DownloadCancelled:
                self.error = "모델 다운로드가 취소되었습니다."
                return False
            except requests.HTTPError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                retry = status in _RETRYABLE_STATUS and attempt < self._max_attempts
                if not retry:
                    self.error = f"{role} 모델 다운로드에 실패했습니다 (HTTP 오류)."
                    return False
            except (requests.RequestException, OSError, ValueError) as exc:
                log.warning("model download attempt failed role=%s attempt=%d error=%s partial_bytes=%d",
                            role, attempt, type(exc).__name__,
                            tmp_path.stat().st_size if tmp_path.exists() else 0)
                if attempt >= self._max_attempts:
                    self.error = f"{role} 모델 다운로드 또는 검증에 실패했습니다."
                    return False
        return False

    def _role(self, filename: str) -> str:
        return "main" if filename == self._profile.model_filename else "mmproj"

    @staticmethod
    def _cleanup_partial(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    # ── SHA-256 / manifest ──────────────────────────────────────────────

    def _is_valid_cached(self, path: Path, expected_sha256: str, expected_size: int) -> bool:
        """파일 크기·mtime 이 manifest 와 일치하면 True (전체 해시 계산 생략)."""
        manifest = self._load_manifest()
        entry = manifest.get(path.name)
        if not entry:
            return False
        stat = path.stat()
        return (
            entry.get("sha256") == expected_sha256.lower()
            and entry.get("size")  == stat.st_size
            and abs(entry.get("mtime", 0) - stat.st_mtime) < 1.0
        )

    @staticmethod
    def _verify_sha256(path: Path, expected: str) -> bool:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                sha.update(chunk)
        return sha.hexdigest() == expected.lower()

    def _load_manifest(self) -> dict:
        if not self._manifest_path.exists():
            return {}
        try:
            return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_manifest(self, filename: str, path: Path, sha256: str) -> None:
        manifest = self._load_manifest()
        stat = path.stat()
        manifest[filename] = {
            "sha256":      sha256.lower(),
            "size":        stat.st_size,
            "mtime":       stat.st_mtime,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("manifest 저장 실패: %s", exc)

    def _remove_manifest(self, filename: str) -> None:
        manifest = self._load_manifest()
        manifest.pop(filename, None)
        try:
            self._manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── 유틸 ────────────────────────────────────────────────────────────

    def _emit_status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _default_models_dir() -> Path:
    from src.utils.app_paths import models_dir
    return Path(models_dir())


def _free_space_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except Exception:
        return 0


def _is_https_url(url: str) -> bool:
    try:
        return urlsplit(url).scheme.lower() == "https"
    except Exception:
        return False


def _safe_url(url: str) -> str:
    """Remove credentials, query parameters, and fragments before logging."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return f"{parts.scheme}://{host}{parts.path}"
    except Exception:
        return "<invalid-url>"


def _header_int(headers, name: str) -> Optional[int]:
    try:
        value = int(headers.get(name, ""))
        return value if value >= 0 else None
    except (TypeError, ValueError):
        return None


_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$", re.IGNORECASE)


def _parse_content_range(value: str) -> Optional[tuple[int, int, int]]:
    match = _CONTENT_RANGE_RE.match((value or "").strip())
    if not match:
        return None
    start, end, total = map(int, match.groups())
    if start > end or end >= total:
        return None
    return start, end, total
