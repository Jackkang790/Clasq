"""Thread-safe immutable snapshot of SQLite-backed searchable file data."""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .search_normalization import search_variants


@dataclass(frozen=True, slots=True)
class SearchRecord:
    normalized_absolute_path: str
    file_id: int | None
    file_name: str
    normalized_filename: str
    compact_filename: str
    normalized_stem: str
    compact_stem: str
    file_path: str
    normalized_path: str
    compact_path: str
    extension: str
    extracted_text: str
    normalized_text: str
    ai_comment: str
    category: str
    normalized_ai_metadata: str
    analysis_status: str
    extract_status: str


@dataclass(frozen=True, slots=True)
class SearchIndexSnapshot:
    records: tuple[SearchRecord, ...]
    generation: int
    build_time_ms: float
    approximate_bytes: int
    document_frequency: dict[str, int]


class _SnapshotState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.generation = 0
        self.snapshot: SearchIndexSnapshot | None = None


_STATES_LOCK = threading.RLock()
_STATES: dict[str, _SnapshotState] = {}


def _db_key(db_path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(db_path)))


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _state_for(db_path: str) -> _SnapshotState:
    key = _db_key(db_path)
    with _STATES_LOCK:
        return _STATES.setdefault(key, _SnapshotState())


def invalidate_search_snapshot(db_path: str) -> int:
    """Mark a database snapshot dirty without mutating snapshots in use."""
    state = _state_for(db_path)
    with state.lock:
        state.generation += 1
        state.snapshot = None
        return state.generation


def _estimate_size(records: tuple[SearchRecord, ...]) -> int:
    total = sys.getsizeof(records)
    for record in records:
        total += sys.getsizeof(record)
        for field_name in SearchRecord.__dataclass_fields__:
            total += sys.getsizeof(getattr(record, field_name))
    return total


def _build_snapshot(db_path: str, generation: int) -> SearchIndexSnapshot:
    started = time.perf_counter()
    connection = sqlite3.connect(db_path, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        files = connection.execute(
            "SELECT id, file_name, file_path, ai_comment, category FROM files"
        ).fetchall()
        cached = connection.execute(
            "SELECT file_path FROM file_fingerprint_cache"
        ).fetchall()
        indexed = connection.execute(
            "SELECT file_path, extracted_text, extract_status FROM file_text_index"
        ).fetchall()
    finally:
        connection.close()

    candidates: dict[str, dict] = {}
    for file_id, name, path, comment, category in files:
        candidates[_normalized_path(path)] = {
            "id": file_id, "file_name": name or os.path.basename(path), "file_path": path,
            "ai_comment": comment or "", "category": category or "",
            "analysis_status": "analyzed", "extracted_text": "", "extract_status": "",
        }
    for (path,) in cached:
        candidates.setdefault(_normalized_path(path), {
            "id": None, "file_name": os.path.basename(path), "file_path": path,
            "ai_comment": "", "category": "", "analysis_status": "pending",
            "extracted_text": "", "extract_status": "",
        })
    for path, text, status in indexed:
        item = candidates.setdefault(_normalized_path(path), {
            "id": None, "file_name": os.path.basename(path), "file_path": path,
            "ai_comment": "", "category": "", "analysis_status": "pending",
            "extracted_text": "", "extract_status": "",
        })
        item["extracted_text"] = (text or "") if status in {"success", "truncated"} else ""
        item["extract_status"] = status or ""

    records = []
    for normalized_absolute_path, item in candidates.items():
        # A scan/DB mutation invalidates the snapshot. During the subsequent
        # rebuild, physically deleted paths must not survive merely because a
        # stale cache row has not yet been pruned.
        if not os.path.exists(item["file_path"]):
            continue
        name, compact_name = search_variants(item["file_name"])
        stem, compact_stem = search_variants(Path(item["file_name"]).stem)
        normalized_path, compact_path = search_variants(item["file_path"])
        extracted_text = item["extracted_text"]
        ai_comment = item["ai_comment"]
        category = item["category"]
        records.append(SearchRecord(
            normalized_absolute_path=normalized_absolute_path,
            file_id=item["id"], file_name=item["file_name"],
            normalized_filename=name, compact_filename=compact_name,
            normalized_stem=stem, compact_stem=compact_stem,
            file_path=item["file_path"], normalized_path=normalized_path,
            compact_path=compact_path,
            extension=Path(item["file_path"]).suffix.casefold(),
            extracted_text=extracted_text, normalized_text=extracted_text.casefold(),
            ai_comment=ai_comment, category=category,
            normalized_ai_metadata=f"{ai_comment} {category}".casefold(),
            analysis_status=item["analysis_status"], extract_status=item["extract_status"],
        ))
    immutable_records = tuple(records)
    document_frequency: dict[str, int] = {}
    token_pattern = re.compile(r"[0-9a-z가-힣]+")
    for record in immutable_records:
        searchable = " ".join((record.normalized_filename, record.normalized_path,
                               record.normalized_text, record.normalized_ai_metadata))
        for token in set(token_pattern.findall(searchable)):
            document_frequency[token] = document_frequency.get(token, 0) + 1
    return SearchIndexSnapshot(
        records=immutable_records,
        generation=generation,
        build_time_ms=(time.perf_counter() - started) * 1000,
        approximate_bytes=_estimate_size(immutable_records),
        document_frequency=document_frequency,
    )


def get_search_snapshot(db_path: str, force_rebuild: bool = False) \
        -> tuple[SearchIndexSnapshot, bool]:
    state = _state_for(db_path)
    with state.lock:
        if not force_rebuild and state.snapshot is not None \
                and state.snapshot.generation == state.generation:
            return state.snapshot, False
        snapshot = _build_snapshot(db_path, state.generation)
        # Atomic reference replacement; readers holding the previous immutable
        # tuple remain safe until their query completes.
        state.snapshot = snapshot
        return snapshot, True


def refresh_search_snapshot(db_path: str) -> SearchIndexSnapshot:
    return get_search_snapshot(db_path, force_rebuild=True)[0]
