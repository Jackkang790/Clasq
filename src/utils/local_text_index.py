"""Incremental, AI-free local document text indexer.

AI 서버(llama-server/Ollama) 없이 동작합니다.
- TextExtractor(file_pipeline.py)로 텍스트를 추출해 file_text_index 테이블에 저장
- mtime_ns + file_size 비교로 변경된 파일만 재색인 (incremental)
- 삭제된 파일 색인 자동 정리
- 색인 변경 시 search snapshot 무효화
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
    """문서 파일의 본문 텍스트를 file_text_index 테이블에 증분 색인한다."""

    # 본문 추출 대상 확장자 (AI 분석 없이도 텍스트 검색 가능한 포맷)
    SUPPORTED_EXTENSIONS = {
        ".pptx", ".pdf", ".docx", ".txt", ".md", ".markdown",
        ".csv", ".json", ".xml", ".yaml", ".yml",
    }
    # .ppt 는 파싱 불가 — 파일명/경로 검색 전용으로 discover_legacy_ppt() 사용
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
        # 스키마 migration 보장 (idempotent)
        FileRegistryManager(db_path=db_path)

    @staticmethod
    def _normalized(path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(path)))

    def _extract_text(self, path: str) -> tuple[str, str, str]:
        """파일 본문 추출. (text, status, extractor_type) 반환.

        status: "success" | "no_text" | "unsupported" | "failed"
        """
        ext = Path(path).suffix.lower()
        extractor_type = self.EXTRACTOR_TYPES.get(ext, "unknown")

        if ext == ".ppt":
            return "", "unsupported", "legacy-ppt"

        if ext not in self.SUPPORTED_EXTENSIONS:
            return "", "unsupported", extractor_type

        try:
            text, raw_status = self.extractor.extract(path)
        except Exception as exc:
            return str(exc), "failed", extractor_type

        if raw_status == "SUCCESS":
            if not text.strip():
                return "", "no_text", extractor_type
            return text, "success", extractor_type
        else:
            # raw_status starts with "ERROR: ..."
            return raw_status, "failed", extractor_type

    @classmethod
    def discover_legacy_ppt(cls, folder_paths: Iterable[str]) -> list[str]:
        """레거시 .ppt 파일 목록 반환 (파일명/경로 검색 전용, 본문 추출 불가)."""
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
        """지정 파일 목록을 증분 색인하고 삭제된 행을 정리한다.

        - mtime_ns + file_size가 동일하면 재색인 생략 (incremental)
        - 변경/신규 파일은 UPSERT로 갱신
        - DB에 색인되어 있지만 실제 파일이 없으면 삭제
        - 색인 변경 발생 시 search snapshot 무효화
        """
        started = time.perf_counter()
        candidates: list[str] = []
        seen: set[str] = set()
        for raw_path in file_paths:
            path = os.path.abspath(os.path.normpath(str(raw_path)))
            if Path(path).suffix.lower() not in self.KNOWN_EXTENSIONS:
                continue
            normalized = self._normalized(path)
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(path)

        stats: dict = {
            "candidates": len(candidates),
            "indexed": 0, "success": 0, "failed": 0,
            "unsupported": 0, "unchanged": 0, "deleted": 0,
            "no_text": 0, "by_extension": {},
        }

        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            # 기존 색인 행 로드 (incremental 비교용)
            existing: dict[str, tuple] = {
                self._normalized(row[0]): row
                for row in conn.execute(
                    "SELECT file_path, file_hash, file_size, file_mtime_ns, extract_status "
                    "FROM file_text_index"
                ).fetchall()
            }
            # fingerprint 캐시 (해시 재계산 최소화)
            fingerprints: dict[str, tuple] = {}
            for table in ("files", "file_fingerprint_cache"):
                try:
                    for row in conn.execute(
                        f"SELECT file_path, file_hash, file_size, file_mtime_ns FROM {table}"
                    ).fetchall():
                        fingerprints[self._normalized(row[0])] = row
                except sqlite3.OperationalError:
                    pass

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            for path in candidates:
                normalized = self._normalized(path)
                ext = Path(path).suffix.lower()
                ext_stats = stats["by_extension"].setdefault(ext, {
                    "eligible": 0, "indexed": 0, "unchanged": 0,
                    "success": 0, "failed": 0, "unsupported": 0, "no_text": 0,
                })
                ext_stats["eligible"] += 1

                try:
                    file_stat = os.stat(path)
                except OSError:
                    stats["failed"] += 1
                    ext_stats["failed"] += 1
                    continue

                # mtime_ns + size 일치 → 변경 없음, 재색인 생략
                old = existing.get(normalized)
                if (old is not None
                        and old[2] == file_stat.st_size
                        and old[3] == file_stat.st_mtime_ns):
                    stats["unchanged"] += 1
                    ext_stats["unchanged"] += 1
                    continue

                # 해시 계산 (fingerprint 캐시 우선 사용)
                fp = fingerprints.get(normalized)
                if (fp is not None
                        and fp[2] == file_stat.st_size
                        and fp[3] == file_stat.st_mtime_ns
                        and fp[1]):
                    file_hash = fp[1]
                else:
                    try:
                        file_hash = self.hash_function(path)
                    except OSError:
                        stats["failed"] += 1
                        ext_stats["failed"] += 1
                        continue

                text, status, extractor_type = self._extract_text(path)

                conn.execute(
                    """
                    INSERT INTO file_text_index (
                        file_path, file_hash, file_size, file_mtime_ns,
                        extracted_text, extractor_type, extract_status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        file_hash      = excluded.file_hash,
                        file_size      = excluded.file_size,
                        file_mtime_ns  = excluded.file_mtime_ns,
                        extracted_text = excluded.extracted_text,
                        extractor_type = excluded.extractor_type,
                        extract_status = excluded.extract_status,
                        updated_at     = excluded.updated_at
                    """,
                    (path, file_hash, file_stat.st_size, file_stat.st_mtime_ns,
                     text, extractor_type, status, now),
                )
                stats["indexed"] += 1
                ext_stats["indexed"] += 1

                if status == "success":
                    stats["success"] += 1
                    ext_stats["success"] += 1
                elif status == "no_text":
                    stats["no_text"] += 1
                    ext_stats["no_text"] += 1
                elif status == "unsupported":
                    stats["unsupported"] += 1
                    ext_stats["unsupported"] += 1
                else:
                    stats["failed"] += 1
                    ext_stats["failed"] += 1

            # 실제로 존재하지 않는 파일 색인 정리
            for (indexed_path,) in conn.execute(
                "SELECT file_path FROM file_text_index"
            ).fetchall():
                if not os.path.exists(indexed_path):
                    conn.execute(
                        "DELETE FROM file_text_index WHERE file_path = ?", (indexed_path,)
                    )
                    stats["deleted"] += 1

            conn.commit()

            if stats["indexed"] or stats["deleted"]:
                try:
                    from .search_snapshot import invalidate_search_snapshot
                    invalidate_search_snapshot(self.db_path)
                except Exception:
                    pass

        finally:
            conn.close()

        stats["elapsed_sec"] = round(time.perf_counter() - started, 3)
        return stats
