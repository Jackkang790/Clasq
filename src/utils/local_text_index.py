"""Incremental, Qwen-free local document text index."""

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
    SUPPORTED_EXTENSIONS = {
        ".pptx", ".pdf", ".docx", ".txt", ".md", ".markdown",
        ".csv", ".json", ".xml", ".yaml", ".yml",
    }
    KNOWN_EXTENSIONS = SUPPORTED_EXTENSIONS | {".ppt"}
    EXTRACTOR_TYPES = {
        ".pptx": "python-pptx", ".pdf": "pypdf", ".docx": "python-docx",
        ".txt": "plain-text", ".md": "plain-text", ".markdown": "plain-text",
        ".csv": "plain-text", ".json": "plain-text", ".xml": "plain-text",
        ".yaml": "plain-text", ".yml": "plain-text",
    }

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
            "no_text": 0, "truncated": 0,
            "by_extension": {},
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
                extension = Path(path).suffix.lower()
                extension_stats = stats["by_extension"].setdefault(extension, {
                    "eligible": 0, "indexed": 0, "unchanged": 0,
                    "success": 0, "failed": 0, "unsupported": 0,
                    "no_text": 0, "truncated": 0,
                })
                extension_stats["eligible"] += 1
                try:
                    file_stat = os.stat(path)
                except OSError:
                    stats["failed"] += 1
                    extension_stats["failed"] += 1
                    continue

                old = existing.get(normalized)
                if (old is not None and old[2] == file_stat.st_size
                        and old[3] == file_stat.st_mtime_ns):
                    stats["unchanged"] += 1
                    extension_stats["unchanged"] += 1
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
                        extension_stats["failed"] += 1
                        continue

                if extension == ".ppt":
                    text, status, extractor_type = "", "unsupported", "legacy-ppt"
                    stats["unsupported"] += 1
                    extension_stats["unsupported"] += 1
                else:
                    try:
                        extract_result = "SUCCESS"
                        if hasattr(self.extractor, "extract_for_index"):
                            text, extract_result = self.extractor.extract_for_index(path)
                            if extract_result not in {"SUCCESS", "TRUNCATED", "NO_TEXT"}:
                                raise RuntimeError(extract_result)
                        elif extension == ".pptx" and hasattr(self.extractor, "_read_pptx"):
                            # Compatibility for existing test/custom PPTX extractors.
                            text = self.extractor._read_pptx(path)
                        else:
                            raise RuntimeError(f"No local extractor for {extension}")
                        status = extract_result.casefold()
                        extractor_type = self.EXTRACTOR_TYPES[extension]
                        if status == "no_text":
                            stats["no_text"] += 1
                            extension_stats["no_text"] += 1
                        elif status == "truncated":
                            stats["success"] += 1
                            stats["truncated"] += 1
                            extension_stats["success"] += 1
                            extension_stats["truncated"] += 1
                        else:
                            stats["success"] += 1
                            extension_stats["success"] += 1
                    except Exception as exc:
                        text = str(exc)
                        status = "failed"
                        extractor_type = self.EXTRACTOR_TYPES.get(extension, "unknown")
                        stats["failed"] += 1
                        extension_stats["failed"] += 1

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
                extension_stats["indexed"] += 1

            indexed_paths = conn.execute("SELECT file_path FROM file_text_index").fetchall()
            for (indexed_path,) in indexed_paths:
                if not os.path.exists(indexed_path):
                    conn.execute(
                        "DELETE FROM file_text_index WHERE file_path = ?", (indexed_path,)
                    )
                    stats["deleted"] += 1
            conn.commit()
            if stats["indexed"] or stats["deleted"]:
                from .search_snapshot import invalidate_search_snapshot
                invalidate_search_snapshot(self.db_path)
        finally:
            conn.close()
        stats["elapsed_sec"] = round(time.perf_counter() - started, 3)
        return stats
