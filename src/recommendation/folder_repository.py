from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

from src.utils.core import DEFAULT_EXCLUDED_DIRECTORIES, load_registered_files

from .models import FileRecommendationContext, SourceFingerprint
from .scope_policy import OrganizationScopePolicy, RootInboxOrganizationPolicy


TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
KEYWORD_STOP_WORDS = {"최종", "파일", "문서", "자료", "복사본", "final", "copy"}


def keywords(value: str, limit: int = 40) -> Tuple[str, ...]:
    seen, result = set(), []
    for token in TOKEN_PATTERN.findall(value.casefold()):
        if token in KEYWORD_STOP_WORDS or token in seen:
            continue
        seen.add(token)
        result.append(token)
        if len(result) >= limit:
            break
    return tuple(result)


@dataclass(frozen=True)
class FolderFileRecord:
    file_path: str
    file_name: str
    extension: str
    category: str
    tags: Tuple[str, ...]
    filename_keywords: Tuple[str, ...]
    text_keywords: Tuple[str, ...]
    summary_keywords: Tuple[str, ...]
    summary: str
    analyzed: bool
    file_hash: str
    file_size: int
    file_mtime_ns: int


class FolderProfileRepository:
    EXCLUDED = frozenset(DEFAULT_EXCLUDED_DIRECTORIES) | frozenset(
        {"_duplicates", "build", "dist", "cache", "tmp"}
    )

    def __init__(self, managed_root: str, db_path: str = "file_manager.db",
                 scope_policy: OrganizationScopePolicy | None = None):
        self.managed_root = os.path.abspath(os.path.normpath(managed_root))
        self.db_path = db_path
        self.scope_policy = scope_policy or RootInboxOrganizationPolicy()

    @staticmethod
    def normalized(path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(path)))

    def is_valid_folder(self, folder_path: str) -> bool:
        path = os.path.abspath(os.path.normpath(folder_path))
        try:
            common = os.path.commonpath([self.managed_root, path])
            if os.path.normcase(common) != os.path.normcase(self.managed_root):
                return False
            if not os.path.isdir(path) or os.path.islink(path):
                return False
            attributes = getattr(os.lstat(path), "st_file_attributes", 0)
            if attributes & 0x400:
                return False
        except (OSError, ValueError):
            return False
        return self.scope_policy.is_destination_folder(path, self.managed_root)

    def is_hard_excluded_path(self, path: str) -> bool:
        checker = getattr(self.scope_policy, "is_hard_excluded_path", None)
        if checker is not None:
            return checker(path, self.managed_root)
        relative = os.path.relpath(path, self.managed_root)
        return any(part.casefold() in self.EXCLUDED for part in Path(relative).parts)

    def load_records(self, scanned_paths: Iterable[str]) -> Tuple[FolderFileRecord, ...]:
        paths = [os.path.abspath(os.path.normpath(path)) for path in scanned_paths]
        allowed = {self.normalized(path) for path in paths}
        analyzed_rows = {
            self.normalized(row["file_path"]): row
            for row in load_registered_files(self.db_path, paths)
        }
        conn = sqlite3.connect(self.db_path)
        try:
            fingerprints = {}
            for table in ("files", "file_fingerprint_cache"):
                for row in conn.execute(
                    f"SELECT file_path,file_hash,file_size,file_mtime_ns FROM {table}"
                ).fetchall():
                    normalized = self.normalized(row[0])
                    if normalized in allowed:
                        fingerprints[normalized] = row[1:]
            indexed = {
                self.normalized(row[0]): row[1]
                for row in conn.execute(
                    "SELECT file_path,extracted_text FROM file_text_index "
                    "WHERE extract_status='success'"
                ).fetchall()
                if self.normalized(row[0]) in allowed
            }
        finally:
            conn.close()

        records = []
        for path in paths:
            normalized = self.normalized(path)
            row = analyzed_rows.get(normalized)
            fingerprint = fingerprints.get(normalized, ("", 0, 0))
            summary = str(row.get("ai_comment", "") or "") if row else ""
            records.append(FolderFileRecord(
                file_path=path,
                file_name=os.path.basename(path),
                extension=Path(path).suffix.casefold().lstrip("."),
                category=(row["category"].lstrip("#") if row else ""),
                tags=tuple(row["tags"]) if row else (),
                filename_keywords=keywords(Path(path).stem),
                text_keywords=keywords(indexed.get(normalized, "")),
                summary_keywords=keywords(summary),
                summary=summary,
                analyzed=bool(row),
                file_hash=fingerprint[0] or "",
                file_size=fingerprint[1] or 0,
                file_mtime_ns=fingerprint[2] or 0,
            ))
        return tuple(records)

    def context_from_record(self, record: FolderFileRecord) -> FileRecommendationContext:
        signals = (
            int(bool(record.filename_keywords))
            + int(bool(record.tags))
            + int(bool(record.category))
            + int(bool(record.text_keywords or record.summary_keywords))
        )
        # Capture current filesystem state; a DB fingerprint may lag behind edits.
        fingerprint = SourceFingerprint.capture(record.file_path, record.file_hash)
        return FileRecommendationContext(
            file_path=record.file_path,
            file_name=record.file_name,
            extension=record.extension,
            current_folder=os.path.dirname(record.file_path),
            filename_keywords=record.filename_keywords,
            tags=record.tags,
            category=record.category,
            text_keywords=tuple(dict.fromkeys(
                record.text_keywords + record.summary_keywords
            )),
            summary=record.summary,
            metadata_coverage=signals / 4.0,
            source_fingerprint=fingerprint,
        )
