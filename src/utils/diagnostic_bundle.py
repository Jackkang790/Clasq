"""Local-only, allowlisted diagnostic bundle export."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .app_paths import logs_dir
from .app_paths import models_dir
from .logging_setup import (
    BACKUP_COUNT,
    LOG_FILENAME,
    MAX_LOG_BYTES,
    flush_runtime_logging,
    redact_text,
)

SUMMARY_SCHEMA_VERSION = 1
BUNDLE_FORMAT_VERSION = 1
LOG_ARCHIVE_NAMES = tuple([LOG_FILENAME] + [f"{LOG_FILENAME}.{i}" for i in range(1, BACKUP_COUNT + 1)])
_ALLOWED_ARCHIVE_ROOTS = {"diagnostic-summary.json", "manifest.json", "README.txt"}
logger = logging.getLogger(__name__)


class DiagnosticExportError(RuntimeError):
    """A user-safe diagnostic export failure."""


@dataclass(frozen=True)
class DiagnosticBundleResult:
    path: Path
    sha256: str
    byte_size: int
    archive_files: tuple[str, ...]


def default_bundle_filename(now: Optional[datetime] = None) -> str:
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"Clasq-Diagnostics-{instant:%Y%m%d-%H%M%SZ}.zip"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_basename(value: object) -> Optional[str]:
    if not value:
        return None
    return str(value).replace("\\", "/").rsplit("/", 1)[-1]


def _model_identifier(model_url: object) -> Optional[str]:
    """Return only the public Hugging Face owner/repository identifier."""
    if not model_url:
        return None
    marker = "huggingface.co/"
    text = str(model_url)
    if marker not in text:
        return None
    parts = text.split(marker, 1)[1].split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else None


def _safe_hardware_summary(detector: Optional[Callable[[], Any]]) -> dict[str, Any]:
    if detector is None:
        return {"status": "not_queried"}
    try:
        info = detector()
        return {
            "status": "available" if getattr(info, "gpu_available", False) else "unavailable",
            "gpu_model": getattr(info, "gpu_name", None),
            "dedicated_vram_mb": getattr(info, "gpu_vram_mb", None),
            "free_vram_mb": getattr(info, "gpu_vram_free_mb", None),
        }
    except Exception:
        return {"status": "query_failed"}


def build_diagnostic_summary(
    *,
    server_manager: Any = None,
    hardware_detector: Optional[Callable[[], Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    profile = getattr(server_manager, "_profile", None) if server_manager is not None else None
    proc = getattr(server_manager, "_proc", None) if server_manager is not None else None
    model_filename = getattr(profile, "model_filename", None)
    mmproj_filename = getattr(profile, "mmproj_filename", None)
    model_path = Path(models_dir()) / model_filename if model_filename else None
    mmproj_path = Path(models_dir()) / mmproj_filename if mmproj_filename else None
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "generated_at": instant.isoformat().replace("+00:00", "Z"),
        "application": {
            "name": "Clasq",
            "version": "unknown",
            "mode": "packaged" if getattr(sys, "frozen", False) else "source",
        },
        "runtime": {
            "os": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
        },
        "hardware": _safe_hardware_summary(
            (lambda: server_manager.hardware_info)
            if server_manager is not None and getattr(server_manager, "hardware_info", None) is not None
            else hardware_detector
        ),
        "local_ai": {
            "ready": bool(getattr(server_manager, "is_available", False)),
            "profile": getattr(profile, "name", None),
            "context_size": getattr(profile, "context_size", None),
            "server_running": bool(proc is not None and getattr(proc, "poll", lambda: 1)() is None),
            "server_pid": getattr(proc, "pid", None),
            "model_identifier": _model_identifier(getattr(profile, "model_url", None)),
            "model_filename": _safe_basename(model_filename),
            "quantization": "Q4_K_M" if model_filename and "q4_k_m" in model_filename.lower() else None,
            "model_expected_sha256": getattr(profile, "model_sha256", None),
            "model_present": bool(model_path and Path(model_path).is_file()),
            "mmproj_filename": _safe_basename(mmproj_filename),
            "mmproj_expected_sha256": getattr(profile, "mmproj_sha256", None),
            "mmproj_present": bool(mmproj_path and Path(mmproj_path).is_file()),
        },
        "logging": {
            "level": logging.getLevelName(logging.getLogger().getEffectiveLevel()),
            "max_bytes": MAX_LOG_BYTES,
            "backup_count": BACKUP_COUNT,
            "encoding": "utf-8",
            "timestamps": "UTC",
        },
        "database": {"schema_version": 3, "included": False},
        "privacy": {
            "user_documents_included": False,
            "database_included": False,
            "model_binaries_included": False,
            "environment_dump_included": False,
        },
    }


def _redact_log_bytes(raw: bytes) -> bytes:
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    identifiers = [os.environ.get("USERNAME", ""), os.environ.get("COMPUTERNAME", "")]
    sanitized_lines = []
    for line in lines:
        clean = redact_text(line)
        for identifier in identifiers:
            if len(identifier) >= 3:
                clean = clean.replace(identifier, "<redacted-identifier>")
        sanitized_lines.append(clean)
    sanitized = "\n".join(sanitized_lines)
    if text.endswith(("\n", "\r")):
        sanitized += "\n"
    return sanitized.encode("utf-8")


def _readme(generated_at: str) -> bytes:
    return (
        "Clasq diagnostic bundle\n"
        f"Generated (UTC): {generated_at}\n\n"
        "This archive contains sanitized Clasq runtime logs and a minimal system summary.\n"
        "It does not contain user documents, database files, model binaries, prompts, or AI responses.\n"
    ).encode("utf-8")


def _verify_zip(path: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(path, "r") as archive:
        if archive.testzip() is not None:
            raise DiagnosticExportError("ZIP verification failed")
        names = tuple(archive.namelist())
        allowed = _ALLOWED_ARCHIVE_ROOTS | {f"logs/{name}" for name in LOG_ARCHIVE_NAMES}
        if not set(names).issubset(allowed) or "manifest.json" not in names:
            raise DiagnosticExportError("ZIP contains an unexpected entry")
        manifest = json.loads(archive.read("manifest.json"))
        for item in manifest["files"]:
            name = item["archive_path"]
            payload = archive.read(name)
            if len(payload) != item["byte_size"] or _sha256_bytes(payload) != item["sha256"]:
                raise DiagnosticExportError("ZIP manifest verification failed")
    return names


def export_diagnostic_bundle(
    destination: os.PathLike | str,
    *,
    log_directory: Optional[os.PathLike | str] = None,
    server_manager: Any = None,
    hardware_detector: Optional[Callable[[], Any]] = None,
    overwrite: bool = False,
    now: Optional[datetime] = None,
) -> DiagnosticBundleResult:
    """Create a verified local ZIP from an explicit allowlist only."""
    final_path = Path(destination)
    if final_path.suffix.lower() != ".zip" or not final_path.parent.is_dir():
        raise DiagnosticExportError("Invalid ZIP destination")
    if final_path.exists() and not overwrite:
        raise FileExistsError(final_path.name)

    logger.info("diagnostic export started")
    flush_runtime_logging()
    source_dir = Path(log_directory) if log_directory is not None else Path(logs_dir())
    temp_zip: Optional[Path] = None
    try:
        with tempfile.TemporaryDirectory(prefix=".clasq-diagnostics-", dir=final_path.parent) as snapshot_root:
            snapshot = Path(snapshot_root)
            payloads: dict[str, bytes] = {}
            summary = build_diagnostic_summary(
                server_manager=server_manager, hardware_detector=hardware_detector, now=now
            )
            generated_at = summary["generated_at"]
            payloads["diagnostic-summary.json"] = (
                json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            payloads["README.txt"] = _readme(generated_at)

            for name in LOG_ARCHIVE_NAMES:
                source = source_dir / name
                try:
                    if source.is_file():
                        # A short-lived snapshot avoids holding a live log open during compression.
                        copied = snapshot / name
                        shutil.copyfile(source, copied)
                        payloads[f"logs/{name}"] = _redact_log_bytes(copied.read_bytes())
                except FileNotFoundError:
                    # Rotation may move a backup between enumeration and copying.
                    continue

            manifest_files = [
                {"archive_path": name, "byte_size": len(data), "sha256": _sha256_bytes(data)}
                for name, data in sorted(payloads.items())
            ]
            manifest = {
                "bundle_format_version": BUNDLE_FORMAT_VERSION,
                "generated_at": generated_at,
                "file_count": len(manifest_files),
                "files": manifest_files,
            }
            payloads["manifest.json"] = (
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")

            handle, temp_name = tempfile.mkstemp(
                prefix=f".{final_path.name}.", suffix=".tmp", dir=final_path.parent
            )
            os.close(handle)
            temp_zip = Path(temp_name)
            with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(payloads.items()):
                    archive.writestr(name, data)
            names = _verify_zip(temp_zip)
            if final_path.exists() and not overwrite:
                raise FileExistsError(final_path.name)
            os.replace(temp_zip, final_path)
            temp_zip = None

        result = DiagnosticBundleResult(
            path=final_path,
            sha256=_sha256_file(final_path),
            byte_size=final_path.stat().st_size,
            archive_files=names,
        )
        logger.info("diagnostic export completed files=%d bytes=%d", len(names), result.byte_size)
        return result
    except (FileExistsError, DiagnosticExportError):
        logger.warning("diagnostic export failed error_type=%s", sys.exc_info()[0].__name__)
        raise
    except Exception as exc:
        logger.warning("diagnostic export failed error_type=%s", type(exc).__name__)
        raise DiagnosticExportError("Diagnostic bundle creation failed") from exc
    finally:
        if temp_zip is not None:
            try:
                temp_zip.unlink(missing_ok=True)
            except Exception:
                pass
