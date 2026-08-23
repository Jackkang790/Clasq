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
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import requests

from .runtime_profile import RuntimeProfile

log = logging.getLogger(__name__)

# 타입: (파일명, 받은 바이트, 전체 바이트) → None
ProgressCallback = Callable[[str, int, int], None]

_CHUNK_SIZE      = 1024 * 1024   # 1 MB
_CONNECT_TIMEOUT = 15
_READ_TIMEOUT    = 60


class ModelDownloader:
    """선택된 RuntimeProfile 에 필요한 모델 파일을 준비한다."""

    def __init__(
        self,
        profile: RuntimeProfile,
        models_dir: Optional[Path] = None,
        on_progress: Optional[ProgressCallback] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ):
        self._profile = profile
        self._models_dir = Path(models_dir) if models_dir else _default_models_dir()
        self._on_progress = on_progress
        self._on_status = on_status
        self._manifest_path = self._models_dir / ".clasq_manifest.json"
        self.error: Optional[str] = None

    # ── 공개 API ────────────────────────────────────────────────────────

    def ensure_ready(self) -> bool:
        """모델 파일이 준비 상태인지 확인하고, 필요하면 다운로드한다.

        항상 bool 을 반환한다. 예외를 던지지 않는다.
        """
        self.error = None
        self._models_dir.mkdir(parents=True, exist_ok=True)

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
        target = self._models_dir / filename
        self._emit_status(f"{filename} 확인 중...")

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
        tmp_path = target.with_suffix(".tmp")
        tmp_path.unlink(missing_ok=True)

        log.info("%s: 다운로드 시작 (%s)", filename, url)
        self._emit_status(f"{filename} 다운로드 중...")

        try:
            resp = requests.get(
                url,
                stream=True,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", expected_size))
            received = 0
            sha = hashlib.sha256()

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    if not chunk:
                        continue
                    f.write(chunk)
                    sha.update(chunk)
                    received += len(chunk)
                    if self._on_progress:
                        self._on_progress(filename, received, total)

        except requests.RequestException as exc:
            self.error = f"{filename} 다운로드 실패: {exc}"
            log.error(self.error)
            tmp_path.unlink(missing_ok=True)
            return False

        # SHA-256 검증 (다운로드 직후 — 항상)
        actual = sha.hexdigest()
        if actual != expected_sha256.lower():
            self.error = (
                f"{filename} SHA-256 불일치.\n"
                f"  예상: {expected_sha256}\n"
                f"  실제: {actual}"
            )
            log.error(self.error)
            tmp_path.unlink(missing_ok=True)
            return False

        # 원자적 이동
        shutil.move(str(tmp_path), str(target))
        self._save_manifest(filename, target, expected_sha256)
        log.info("%s: 다운로드 및 검증 완료", filename)
        return True

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
    local_appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(local_appdata) / "Clasq" / "models"


def _free_space_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except Exception:
        return 0
