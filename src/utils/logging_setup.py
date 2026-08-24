"""Bounded, privacy-conscious application runtime logging for Clasq."""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from .app_paths import logs_dir

LOG_FILENAME = "clasq.log"
DEFAULT_LEVEL = logging.INFO
MAX_LOG_BYTES = 4 * 1024 * 1024
BACKUP_COUNT = 4
MAX_RETAINED_BYTES = MAX_LOG_BYTES * (BACKUP_COUNT + 1)
_ALLOWED_LEVELS = {
    "DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING,
    "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL,
}
_HANDLER_MARKER = "_clasq_runtime_file_handler"
_session_id = uuid.uuid4().hex[:8]

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([a-z0-9_-]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"password|passwd|pfx[_-]?password|authorization))\b(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|password|signature)=)[^&#\s]+"
)
_WINDOWS_USER_PATH = re.compile(
    r"(?i)\b[A-Z]:\\Users\\[^\\\r\n]+(?:\\[^\r\n\t<>|\"?*]+)+"
)


def resolve_log_level(value: Optional[str] = None) -> int:
    raw = value if value is not None else os.environ.get("CLASQ_LOG_LEVEL", "INFO")
    return _ALLOWED_LEVELS.get(str(raw).strip().upper(), DEFAULT_LEVEL)


def sanitize_text(value: object) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return "".join(ch if ch >= " " else "?" for ch in text)


def safe_filename(path: object) -> str:
    text = str(path).replace("\\", "/")
    return sanitize_text(text.rsplit("/", 1)[-1])


def redact_text(value: object) -> str:
    text = sanitize_text(value)
    text = _BEARER.sub("Bearer <redacted>", text)
    text = _SECRET_ASSIGNMENT.sub(r"\1\2<redacted>", text)
    text = _SENSITIVE_QUERY.sub(r"\1<redacted>", text)
    return _WINDOWS_USER_PATH.sub("<user-path>", text)


class PrivacyFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Disable only file logging if write or rollover fails."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except Exception:
            self.disabled = True

    def handleError(self, record: logging.LogRecord) -> None:
        # Base logging prints a traceback in development and then retries every
        # record.  Disable this handler quietly after the first I/O failure.
        self.disabled = True

    def handle(self, record: logging.LogRecord) -> bool:
        if getattr(self, "disabled", False):
            return False
        return super().handle(record)


def _formatter() -> logging.Formatter:
    return PrivacyFormatter(
        "%(asctime)sZ %(levelname)s %(name)s pid=%(process)d thread=%(threadName)s "
        f"session={_session_id} %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def initialize_runtime_logging(
    *, log_directory: Optional[os.PathLike | str] = None,
    level: Optional[str] = None,
    max_bytes: int = MAX_LOG_BYTES,
    backup_count: int = BACKUP_COUNT,
) -> Optional[Path]:
    """Install exactly one file handler; return ``None`` on setup failure."""
    root = logging.getLogger()
    resolved_level = resolve_log_level(level)
    root.setLevel(resolved_level)
    existing = [h for h in root.handlers if getattr(h, _HANDLER_MARKER, False)]
    if existing:
        for handler in existing:
            handler.setLevel(resolved_level)
        return Path(existing[0].baseFilename)
    try:
        directory = Path(log_directory) if log_directory is not None else Path(logs_dir())
        directory.mkdir(parents=True, exist_ok=True)
        handler = SafeRotatingFileHandler(
            directory / LOG_FILENAME, maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8", delay=True,
        )
        setattr(handler, _HANDLER_MARKER, True)
        handler.setLevel(resolved_level)
        handler.setFormatter(_formatter())
        root.addHandler(handler)
        return directory / LOG_FILENAME
    except Exception as exc:
        if not getattr(sys, "frozen", False) and sys.stderr is not None:
            try:
                sys.stderr.write(f"Clasq file logging unavailable: {type(exc).__name__}\n")
            except Exception:
                pass
        return None


def shutdown_runtime_logging() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if not getattr(handler, _HANDLER_MARKER, False):
            continue
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
        finally:
            root.removeHandler(handler)


def flush_runtime_logging() -> None:
    """Flush Clasq's file handler without stopping subsequent logging."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if not getattr(handler, _HANDLER_MARKER, False):
            continue
        try:
            handler.acquire()
            handler.flush()
        except Exception:
            # Diagnostics must never make the application unavailable.
            pass
        finally:
            try:
                handler.release()
            except Exception:
                pass


def runtime_session_id() -> str:
    return _session_id
