"""Incremental, Qwen-free local document text index.

PPTX is the first supported extractor. The service deliberately has no AI
dependencies and can be extended with additional local extractors later.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from .db_manager import FileRegistryManager
from .file_pipeline import TextExtractor
from .core import DEFAULT_EXCLUDED_DIRECTORIES


class LocalTextIndexer:
    SUPPORTED_EXTENSIONS = {".pptx"}
    KNOWN_EXTENSIONS = {".ppt", ".pptx"}

    def __init__(
        self,
        db_path: str = "file_manager.db",
        extractor: Optional[TextExtractor] = None,
        hash_function: Optional[Callable[[str], str]] = None,
    ):
        self.db_path = db_path
        self.extractor = extractor or TextExtractor()
        self.hash_function = hash_function or FileRegistryManager.compute_file_hash
        # Runs the idempotent schema migration before direct SQLite access.
        FileRegistryManager(db_path=db_path)

    @staticmethod
    def _normalized(path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(path)))

    @classmethod
    def discover_legacy_ppt(cls, folder_paths: Iterable[str]) -> list[str]:
        """Find legacy PPT files for filename/path search, not AI analysis."""
        excluded = {name.casefold() for name in DEFAULT_EXCLUDED_DIRECTORIES}
        paths = []
        for folder_path in folder_paths:
            for current_root, directories, names in os.walk(
                folder_path, topdown=True, onerror=lambda _error: None, followlinks=False
            ):
                directories[:] = [
                    name for name in directories
                    if name.casefold() not in excluded
                    and not (Path(current_root) / name).is_symlink()
                ]
                paths.extend(
                    str((Path(current_root) / name).resolve())
                    for name in names if Path(name).suffix.lower() == ".ppt"
                )
        return sorted(set(paths), key=str.casefold)

    def synchronize(self, file_paths: Iterable[str]) -> dict:
        """Index new/changed PPTX files and prune physically missing rows."""
        started = time.perf_counter()
        candidates = []
        seen = set()
        for raw_path in file_paths:
            path = os.path.abspath(os.path.normpath(str(raw_path)))
            if Path(path).suffix.lower() not in self.KNOWN_EXTENSIONS:
                continue
            normalized = self._normalized(path)
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(path)

        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        stats = {
            "candidates": len(candidates), "indexed": 0, "success": 0,
            "failed": 0, "unsupported": 0, "unchanged": 0, "deleted": 0,
        }
        try:
            existing = {
                self._normalized(row[0]): row
                for row in conn.execute(
                    "SELECT file_path, file_hash, file_size, file_mtime_ns, extract_status "
                    "FROM file_text_index"
                ).fetchall()
            }
            fingerprints = {}
            for table in ("files", "file_fingerprint_cache"):
                for row in conn.execute(
                    f"SELECT file_path, file_hash, file_size, file_mtime_ns FROM {table}"
                ).fetchall():
                    fingerprints[self._normalized(row[0])] = row

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            for path in candidates:
                normalized = self._normalized(path)
                try:
                    file_stat = os.stat(path)
                except OSError:
                    stats["failed"] += 1
                    continue

                old = existing.get(normalized)
                if (old is not None and old[2] == file_stat.st_size
                        and old[3] == file_stat.st_mtime_ns):
                    stats["unchanged"] += 1
                    continue

                fingerprint = fingerprints.get(normalized)
                if (fingerprint is not None and fingerprint[2] == file_stat.st_size
                        and fingerprint[3] == file_stat.st_mtime_ns
                        and fingerprint[1]):
                    file_hash = fingerprint[1]
                else:
                    try:
                        file_hash = self.hash_function(path)
                    except OSError:
                        stats["failed"] += 1
                        continue

                extension = Path(path).suffix.lower()
                if extension == ".ppt":
                    text, status, extractor_type = "", "unsupported", "legacy-ppt"
                    stats["unsupported"] += 1
                else:
                    try:
                        # Reuse the existing python-pptx extraction implementation.
                        text = self.extractor._read_pptx(path)
                        status, extractor_type = "success", "python-pptx"
                        stats["success"] += 1
                    except Exception as exc:
                        text = str(exc)
                        status, extractor_type = "failed", "python-pptx"
                        stats["failed"] += 1

                conn.execute(
                    """
                    INSERT INTO file_text_index (
                        file_path, file_hash, file_size, file_mtime_ns,
                        extracted_text, extractor_type, extract_status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        file_hash=excluded.file_hash,
                        file_size=excluded.file_size,
                        file_mtime_ns=excluded.file_mtime_ns,
                        extracted_text=excluded.extracted_text,
                        extractor_type=excluded.extractor_type,
                        extract_status=excluded.extract_status,
                        updated_at=excluded.updated_at
                    """,
                    (path, file_hash, file_stat.st_size, file_stat.st_mtime_ns,
                     text, extractor_type, status, now),
                )
                stats["indexed"] += 1

            indexed_paths = conn.execute("SELECT file_path FROM file_text_index").fetchall()
            for (indexed_path,) in indexed_paths:
                if not os.path.exists(indexed_path):
                    conn.execute(
                        "DELETE FROM file_text_index WHERE file_path = ?", (indexed_path,)
                    )
                    stats["deleted"] += 1
            conn.commit()
        finally:
            conn.close()
        stats["elapsed_sec"] = round(time.perf_counter() - started, 3)
        return stats
