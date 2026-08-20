"""Small read-only helpers used by the organize screen."""

from __future__ import annotations

import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional


SUPPORTED_EXTENSIONS = {
    ".txt", ".pdf", ".docx", ".xlsx", ".pptx", ".hwp", ".hwpx",
    ".csv", ".json", ".xml", ".yaml", ".yml", ".html", ".htm",
    ".md", ".markdown", ".jpg", ".jpeg", ".png", ".webp", ".bmp",
    ".gif", ".tiff", ".tif", ".mp3", ".mp4", ".wav", ".m4a",
    ".mkv", ".avi",
}

DEFAULT_EXCLUDED_DIRECTORIES = {
    ".git", ".idea", "node_modules", ".venv", "venv", "__pycache__",
}


def scan_directory_files(
    directory: str,
    excluded_directories: Optional[Iterable[str]] = None,
) -> list[str]:
    """Return supported files below *directory* using recursive traversal."""
    root = Path(directory).expanduser()
    if not root.is_dir():
        return []
    excluded = {
        name.casefold()
        for name in (excluded_directories or DEFAULT_EXCLUDED_DIRECTORIES)
    }
    files = []
    # followlinks=False avoids symlink/junction recursion. Pruning dirs in-place
    # prevents expensive traversal of dependency and IDE metadata trees.
    for current_root, directories, names in os.walk(
        root, topdown=True, onerror=lambda _error: None, followlinks=False
    ):
        kept_directories = []
        for name in directories:
            path = Path(current_root) / name
            try:
                attributes = getattr(os.lstat(path), "st_file_attributes", 0)
                is_reparse_point = bool(attributes & 0x400)
            except OSError:
                is_reparse_point = True
            if (name.casefold() not in excluded and not path.is_symlink()
                    and not is_reparse_point):
                kept_directories.append(name)
        directories[:] = kept_directories
        for name in names:
            path = Path(current_root) / name
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(str(path.resolve()))
    return sorted(files, key=str.casefold)


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _has_analysis(ai_comment: str, category: str) -> bool:
    """Treat a row as analyzed only when it contains stored AI metadata."""
    return bool((ai_comment or "").strip() or (category or "").strip())


def build_incremental_analysis_plan(
    file_paths: Iterable[str],
    db_path: str = "file_manager.db",
    hash_function=None,
) -> dict:
    """Classify files using stat fingerprints before canonical SHA-256."""
    from .db_manager import FileRegistryManager

    registry = FileRegistryManager(db_path=db_path)
    if hash_function is None:
        hash_function = registry.compute_file_hash

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT file_path, file_hash, file_size, file_mtime_ns,
                   ai_comment, category
            FROM files
            """
        ).fetchall()
        cached_rows = connection.execute(
            """
            SELECT file_path, file_hash, file_size, file_mtime_ns
            FROM file_fingerprint_cache
            """
        ).fetchall()
    finally:
        connection.close()

    by_path = {}
    analyzed_by_hash = defaultdict(list)
    for file_path, file_hash, file_size, file_mtime_ns, ai_comment, category in rows:
        record = {
            "file_path": file_path,
            "file_hash": file_hash or "",
            "file_size": file_size,
            "file_mtime_ns": file_mtime_ns,
            "ai_comment": ai_comment or "",
            "category": category or "",
            "analyzed": _has_analysis(ai_comment, category),
            "source": "files",
        }
        by_path[_normalized_path(file_path)] = record
        if record["file_hash"] and record["analyzed"]:
            analyzed_by_hash[record["file_hash"]].append(record)
    for file_path, file_hash, file_size, file_mtime_ns in cached_rows:
        normalized = _normalized_path(file_path)
        if normalized not in by_path:
            by_path[normalized] = {
                "file_path": file_path,
                "file_hash": file_hash or "",
                "file_size": file_size,
                "file_mtime_ns": file_mtime_ns,
                "ai_comment": "",
                "category": "",
                "analyzed": False,
                "source": "cache",
            }

    plan = {
        "scanned": [], "already_analyzed": [], "new": [], "changed": [],
        "same_content": [], "incomplete": [], "pending": [], "errors": [],
    }
    metrics = {
        "stat_only_skipped": 0,
        "sha256_calculated": 0,
        "hash_backfilled": 0,
        "changed_candidates": 0,
        "hash_errors": 0,
    }
    cache_updates = []
    file_row_updates = []
    for raw_path in file_paths:
        file_path = os.path.abspath(os.path.normpath(raw_path))
        plan["scanned"].append(file_path)
        try:
            file_stat = os.stat(file_path)
        except Exception as exc:
            plan["errors"].append({"file_path": file_path, "error": str(exc)})
            metrics["hash_errors"] += 1
            continue

        existing = by_path.get(_normalized_path(file_path))
        fingerprint_matches = bool(
            existing
            and existing["file_hash"]
            and existing["file_size"] == file_stat.st_size
            and existing["file_mtime_ns"] == file_stat.st_mtime_ns
        )
        if fingerprint_matches:
            metrics["stat_only_skipped"] += 1
            item = {
                "file_path": file_path,
                "file_hash": existing["file_hash"],
                "file_size": file_stat.st_size,
                "file_mtime_ns": file_stat.st_mtime_ns,
            }
            if existing["analyzed"]:
                plan["already_analyzed"].append(item)
            else:
                item["reason"] = "new"
                plan["new"].append(item)
                plan["pending"].append(item)
            continue

        if existing is not None:
            metrics["changed_candidates"] += 1
        try:
            file_hash = hash_function(file_path)
            metrics["sha256_calculated"] += 1
        except Exception as exc:
            plan["errors"].append({"file_path": file_path, "error": str(exc)})
            metrics["hash_errors"] += 1
            continue

        item = {
            "file_path": file_path,
            "file_hash": file_hash,
            "file_size": file_stat.st_size,
            "file_mtime_ns": file_stat.st_mtime_ns,
        }
        if existing is not None:
            if existing["file_hash"] == file_hash and existing["analyzed"]:
                plan["already_analyzed"].append(item)
                file_row_updates.append(item)
                continue
            if existing["file_hash"] == file_hash:
                item["reason"] = "new"
                plan["new"].append(item)
                if existing["source"] == "files":
                    file_row_updates.append(item)
                else:
                    cache_updates.append(item)
            else:
                item["reason"] = "changed"
                plan["changed"].append(item)
            plan["pending"].append(item)
            continue

        reusable = analyzed_by_hash.get(file_hash)
        if reusable:
            item["source_file_path"] = reusable[0]["file_path"]
            plan["same_content"].append(item)
            continue

        item["reason"] = "new"
        plan["new"].append(item)
        plan["pending"].append(item)
        cache_updates.append(item)

    metrics["hash_backfilled"] = (
        registry.backfill_file_fingerprints(file_row_updates)
        + registry.cache_file_fingerprints(cache_updates)
    )

    plan["counts"] = {
        "scanned": len(plan["scanned"]),
        "already_analyzed": len(plan["already_analyzed"]),
        "new": len(plan["new"]),
        "changed": len(plan["changed"]),
        "same_content": len(plan["same_content"]),
        "incomplete": len(plan["incomplete"]),
        "pending": len(plan["pending"]),
        "errors": len(plan["errors"]),
    }
    plan["performance"] = metrics
    return plan


def _tags_from_record(ai_comment: str, category: str) -> list[str]:
    tags = []
    tag_section = (ai_comment or "").split("/", 1)[0]
    for match in re.findall(r"#([^,#/]+)", tag_section):
        tag = match.strip()
        if tag and tag not in {"일반", "분석실패"} and tag not in tags:
            tags.append(tag)
    fallback = (category or "").lstrip("#").strip()
    if not tags and fallback and fallback not in {"일반", "분석실패"}:
        tags.append(fallback)
    return tags


def load_registered_files(
    db_path: str = "file_manager.db",
    file_paths: Optional[Iterable[str]] = None,
) -> list[dict]:
    """Load existing DB file rows without changing the schema."""
    if not os.path.exists(db_path):
        return []
    allowed = {os.path.abspath(path) for path in file_paths} if file_paths is not None else None
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT id, file_name, file_path, ai_comment, category FROM files ORDER BY file_name"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()

    result = []
    for file_id, file_name, file_path, ai_comment, category in rows:
        normalized = os.path.abspath(file_path)
        if allowed is not None and normalized not in allowed:
            continue
        result.append({
            "id": file_id,
            "file_name": file_name,
            "file_path": file_path,
            "ai_comment": ai_comment or "",
            "category": category or "",
            "tags": _tags_from_record(ai_comment or "", category or ""),
        })
    return result


def get_files_for_organize(
    db_path: str = "file_manager.db",
    file_paths: Optional[Iterable[str]] = None,
) -> list[dict]:
    """Return only analyzed DB rows that have at least one usable tag."""
    return [row for row in load_registered_files(db_path, file_paths) if row["tags"]]
