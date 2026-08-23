# =========================================================
# [core.py]
# 통합 코어 모듈 - 파일 관리 시스템의 핵심 기능을 결합
# =========================================================
import os
import shutil
from typing import Dict, Any, List

from .file_pipeline import TextExtractor, FileAnalyzer, ExtensionTagger
from .query_parser import SearchQueryParser
from .db_manager import FileRegistryManager
from .search_engine import SearchEngine
from .config import SUPPORTED_EXTENSIONS


class ClasqCore:
    """
    통합 코어 클래스
    파일 분석, DB 관리, 검색 엔진을 하나로 결합하여
    시스템의 핵심 기능을 제공합니다.
    """

    def __init__(
        self,
        db_path: str = "file_manager.db",
        ollama_url: str = "http://localhost:11434",
        text_model: str = "gemma2:9b",
        vision_model: str = "llava",
    ):
        """
        코어 시스템 초기화
        
        Args:
            db_path: SQLite DB 파일 경로
            ollama_url: Ollama API URL
            text_model: 텍스트 분석용 모델
            vision_model: 이미지 분석용 모델
        """
        self.db_path = db_path
        
        # DB 관리자 초기화
        self.registry = FileRegistryManager(db_path=db_path)
        
        # 파일 파이프라인 초기화
        self.extractor = TextExtractor()
        self.analyzer = FileAnalyzer(
            ollama_url=ollama_url,
            text_model=text_model,
            vision_model=vision_model
        )
        
        # 검색 파서 초기화
        self.query_parser = SearchQueryParser(
            ollama_url=ollama_url,
            model=text_model
        )
        
        # 검색 엔진 초기화
        self.search_engine = SearchEngine(db_path=db_path)

    def _normalize_path(self, path: str) -> str:
        """경로 문자열 정제 (윈도우 경로 깨짐 방어)"""
        if not path:
            return ""
        clean_path = path.replace('￥', '/').replace('\\', '/')
        return os.path.abspath(clean_path)

    def _is_duplicate_path(self, file_path: str) -> bool:
        """격리 폴더 내부 파일은 재태깅·재정리 대상에서 제외합니다."""
        return self.registry.duplicates_dir_name.casefold() in {
            part.casefold() for part in os.path.normpath(file_path).split(os.sep)
        }

    def _save_to_db(self, file_path: str, metadata_result: Dict[str, Any]) -> Dict[str, Any]:
        """AI 분석 결과를 DB에 저장"""
        result = self.registry.save_file_result(file_path, metadata_result)
        if not result.get("success"):
            print(f"[DB 저장 오류]: {result.get('message')}")
        elif result.get("is_duplicate"):
            print(f"[중복 파일 감지]: {file_path} -> {result.get('duplicate_of')} 와 내용 동일 "
                  f"(정책: {self.registry.duplicate_policy})")
        return result

    def sync_db_with_disk(self):
        """DB와 실제 디스크 파일 동기화"""
        return self.registry.sync_missing_files()

    # ---------------------------------------------------------
    # [유스케이스 1] 파일 업로드 및 분석 요청 처리
    # ---------------------------------------------------------
    def process_file_upload(self, raw_file_path: str) -> Dict[str, Any]:
        """
        파일 처리 파이프라인
        경로 정제 -> 파일 종류 판별 -> 전처리 -> AI 분석 -> DB 저장
        """
        file_path = self._normalize_path(raw_file_path)

        if not os.path.exists(file_path):
            return {
                "@TYPE": "@ERROR",
                "message": f"파일을 찾을 수 없습니다: {file_path}"
            }

        extension = os.path.splitext(file_path)[1].lower()

        # A-1. 오디오: 내용 분석 미지원 (모든 AI 모드에서 기본 태그만 부착)
        if extension in ExtensionTagger.AUDIO_EXTENSIONS:
            res = self.analyzer._build_fallback_response(
                {"original_name": os.path.basename(file_path), "file_path": file_path},
                "오디오 파일은 내용 분석을 지원하지 않습니다.")

        # A-2. 영상: llama_server 모드에서 VideoAnalyzer 사용, ollama 모드에서는 오류 응답
        elif extension in ExtensionTagger.VIDEO_EXTENSIONS:
            res = self.analyzer.analyze_video(file_path)

        # B. 이미지 파일 처리
        elif self.extractor.is_image_file(file_path):
            img_bytes, status = self.extractor.process_image(file_path)
            if status != "SUCCESS":
                res = self.analyzer._build_fallback_response(
                    {"original_name": os.path.basename(file_path), "file_path": file_path}, status)
            else:
                res = self.analyzer.analyze_image_bytes(file_path, img_bytes)

        # C. 일반 문서/데이터 파일 처리
        else:
            text, status = self.extractor.extract(file_path)
            if status != "SUCCESS":
                res = self.analyzer._build_fallback_response(
                    {"original_name": os.path.basename(file_path), "file_path": file_path}, status)
            else:
                res = self.analyzer.analyze_document_text(file_path, text)

        # 분석 결과를 DB에 저장하고, 저장 실패도 호출자에게 명확히 알린다.
        db_result = self._save_to_db(file_path, res)
        if not db_result.get("success"):
            res["status"] = "FAILED"
            res["error"] = db_result.get("message", "분석 결과를 DB에 저장하지 못했습니다.")
        res["db_result"] = db_result

        # DB 변경 후 검색 snapshot을 무효화한다 (다음 검색 시 재빌드됨).
        # invalidate 자체는 O(1) 이므로 대량 분석 중에도 부담이 없다.
        try:
            self.search_engine.invalidate_snapshot()
        except Exception:
            pass  # snapshot 무효화 실패가 파일 저장 결과에 영향을 주지 않도록

        return res

    # ---------------------------------------------------------
    # [유스케이스 2] 자연어 검색창 입력문 처리
    # ---------------------------------------------------------
    def process_user_query(self, user_text: str) -> Dict[str, Any]:
        """
        사용자 자연어 입력 처리
        의도 파싱 -> 검색 엔진 전달 -> 결과 반환
        """
        # 1단계: 자연어 의도 파싱
        parse_result = self.query_parser.parse_user_query(user_text)
        
        if parse_result.get("status") != "SUCCESS":
            return {
                "@TYPE": "@ERROR",
                "message": parse_result.get("data", {}).get("message", "자연어 파싱 실패")
            }
        
        parsed_data = parse_result.get("data", {})
        
        # 2단계: 검색 엔진으로 결과 처리
        return self.search_engine.process_query_result(parsed_data)

    # ---------------------------------------------------------
    # [유스케이스 3] 폴더 배치 처리
    # ---------------------------------------------------------
    def process_folder_batch(self, folder_path: str, progress_callback=None) -> Dict[str, Any]:
        """
        폴더 내 파일들을 일괄 처리
        progress_callback: 진행 상황을 전달받을 콜백 함수
        """
        folder_path = self._normalize_path(folder_path)
        
        if not os.path.exists(folder_path):
            return {
                "@TYPE": "@ERROR",
                "message": f"폴더를 찾을 수 없습니다: {folder_path}"
            }

        files_to_process = [item["file_path"] for item in self.scan_directory_files(folder_path)]
        
        if not files_to_process:
            return {
                "@TYPE": "@ERROR",
                "message": "스캔할 지원 파일이 지정된 경로에 없습니다."
            }

        total_count = len(files_to_process)
        success_count = 0
        error_count = 0
        
        # 대량 처리를 위한 DB 세션 시작
        with self.registry.bulk_session():
            for idx, file_path in enumerate(files_to_process, start=1):
                file_name = os.path.basename(file_path)
                
                if progress_callback:
                    progress_callback(f"AI 분석 중 ({idx}/{total_count}): {file_name}")
                
                try:
                    result = self.process_file_upload(file_path)
                    if result.get("status") == "SUCCESS":
                        success_count += 1
                    else:
                        error_count += 1
                except Exception as e:
                    print(f"파일 처리 실패 ({file_name}): {str(e)}")
                    error_count += 1
        
        # 처리 완료 후 DB 동기화
        removed_files = self.sync_db_with_disk()
        
        return {
            "@TYPE": "@SUCCESS",
            "message": f"폴더 처리 완료: 성공 {success_count}개, 실패 {error_count}개",
            "total_files": total_count,
            "success_count": success_count,
            "error_count": error_count,
            "removed_files": len(removed_files)
        }

    # ---------------------------------------------------------
    # [유틸리티] DB 상태 조회
    # ---------------------------------------------------------
    def get_db_stats(self) -> Dict[str, Any]:
        """DB 현재 상태 통계 정보 반환"""
        conn = self.registry._get_conn()
        try:
            cursor = conn.cursor()
            
            # 전체 파일 수
            cursor.execute("SELECT COUNT(*) FROM files")
            total_files = cursor.fetchone()[0]
            
            # 카테고리별 분포
            cursor.execute("""
                SELECT category, COUNT(*) as count 
                FROM files 
                WHERE category IS NOT NULL AND category != ''
                GROUP BY category
            """)
            category_stats = {row[0]: row[1] for row in cursor.fetchall()}
            
            return {
                "total_files": total_files,
                "category_distribution": category_stats,
                "db_path": self.db_path
            }
        finally:
            if self.registry._bulk_conn is None:
                conn.close()

    # ---------------------------------------------------------
    # [유스케이스 5] 태그 기반 파일 정리
    # ---------------------------------------------------------
    def scan_directory_files(self, directory: str) -> List[Dict[str, Any]]:
        """디렉터리에서 현재 파이프라인이 지원하는 파일을 재귀적으로 스캔합니다."""
        files: List[Dict[str, Any]] = []
        directory = self._normalize_path(directory)

        if not os.path.isdir(directory) or self._is_duplicate_path(directory):
            return files
 
        for root, dirs, filenames in os.walk(directory):
            # 중복 격리 폴더는 어떤 깊이에 있더라도 재탐색하지 않는다.
            dirs[:] = [name for name in dirs if name != self.registry.duplicates_dir_name]
            for filename in filenames:
                if filename.lower().endswith(SUPPORTED_EXTENSIONS):
                    file_path = os.path.join(root, filename)
                    files.append({
                        "file_name": filename,
                        "file_path": file_path,
                        "tags": [],
                        "category": "#미분류"
                    })
 
        return files

    def get_saved_files(self) -> List[Dict[str, Any]]:
        """저장 목록 화면에 표시할 실제 분석 DB 레코드를 반환합니다."""
        return self.registry.list_files()

    def update_saved_file(self, file_id: int, display_name: str, tags: str, description: str) -> Dict[str, Any]:
        return self.registry.update_file_metadata(file_id, display_name, tags, description)

    def build_organize_preview(self, groups: Dict[str, List[Dict[str, Any]]], base_path: str) -> List[Dict[str, Any]]:
        """파일을 옮기지 않고 대상 경로와 이름 충돌 여부를 계산합니다."""
        base_path = self._normalize_path(base_path)
        preview, planned_paths = [], set()
        for tag_name, files in groups.items():
            safe_tag = "".join(c for c in tag_name if c not in r'\\/:*?\"<>|').strip()
            if not safe_tag:
                continue
            target_dir = os.path.join(base_path, safe_tag)
            for item in files:
                candidate = os.path.join(target_dir, item["file_name"])
                conflict = os.path.exists(candidate) or os.path.normcase(candidate) in planned_paths
                planned_paths.add(os.path.normcase(candidate))
                preview.append({"tag": safe_tag, "file_name": item["file_name"],
                                "source_path": item["file_path"], "target_path": candidate,
                                "has_conflict": conflict})
        return preview
    
    def get_files_for_organize(self) -> List[Dict[str, Any]]:
        """태그가 있는 DB 파일을 정리 화면용 데이터로 조회합니다."""
        self.sync_db_with_disk()
        conn = self.registry._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, file_name, file_path, tags, category
                FROM files
                WHERE tags IS NOT NULL AND tags != ''
                ORDER BY category, file_name
                """
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "file_name": row[1],
                    "file_path": row[2],
                    "tags": row[3].split(",") if row[3] else [],
                    "category": row[4],
                }
                for row in rows if not self._is_duplicate_path(row[2])
            ]
        finally:
            if self.registry._bulk_conn is None:
                conn.close()

    @staticmethod
    def group_files_by_tags(files: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """첫 번째 태그를 기준으로 파일을 그룹화합니다."""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for file_info in files:
            tags = file_info.get("tags", [])
            if not tags:
                continue
            tag_name = tags[0].strip().lstrip("#").strip()
            if tag_name:
                groups.setdefault(tag_name, []).append(file_info)
        return groups

    @staticmethod
    def _available_destination(directory: str, file_name: str) -> str:
        """기존 파일을 덮어쓰지 않는 이동 대상 경로를 만듭니다."""
        candidate = os.path.join(directory, file_name)
        if not os.path.exists(candidate):
            return candidate
        stem, extension = os.path.splitext(file_name)
        index = 1
        while os.path.exists(candidate):
            candidate = os.path.join(directory, f"{stem} ({index}){extension}")
            index += 1
        return candidate

    def organize_files(
        self, groups: Dict[str, List[Dict[str, Any]]], base_path: str
    ) -> Dict[str, Any]:
        """태그별 폴더로 파일을 이동하고 DB 경로를 함께 갱신합니다."""
        base_path = self._normalize_path(base_path)
        if not os.path.isdir(base_path):
            return {"success": False, "message": f"기본 경로가 존재하지 않습니다: {base_path}", "errors": []}

        moved_files: List[Dict[str, str]] = []
        errors: List[str] = []
        for tag_name, files in groups.items():
            safe_tag = "".join(char for char in tag_name if char not in r'\\/:*?\"<>|').strip()
            if not safe_tag:
                errors.append(f"사용할 수 없는 태그 이름: {tag_name}")
                continue
            target_dir = os.path.join(base_path, safe_tag)
            try:
                os.makedirs(target_dir, exist_ok=True)
            except OSError as exc:
                errors.append(f"폴더 생성 실패 ({target_dir}): {exc}")
                continue
            for file_info in files:
                result = self.registry.move_file_safely(file_info["id"], target_dir)
                if result["success"]:
                    moved_files.append({"old_path": result["old_path"], "new_path": result["new_path"], "tag": safe_tag})
                else:
                    errors.append(result["message"])
        return {
            "success": bool(moved_files) or not errors,
            "message": f"파일 정리 완료: 성공 {len(moved_files)}개, 실패 {len(errors)}개",
            "moved_files": moved_files,
            "errors": errors,
        }
    def get_all_files(self) -> List[Dict[str, Any]]:
        """저장 목록 화면용 - DB의 모든 파일 조회"""
        return self.registry.get_all_files()
     
# =========================================================
# 하위 호환성을 위한 별칭 클래스
# =========================================================
class MainProcessor(ClasqCore):
    """기존 코드와의 호환성을 위한 별칭 클래스"""
    pass


# =========================================================
# Batch 5: recommendation 패키지가 필요로 하는 공통 상수 / 헬퍼
# =========================================================

import sqlite3 as _sqlite3
from typing import Iterable as _Iterable, Optional as _Optional

DEFAULT_EXCLUDED_DIRECTORIES = {
    ".git", ".idea", "node_modules", ".venv", "venv", "__pycache__",
}


def load_registered_files(
    db_path: str = "file_manager.db",
    file_paths: _Optional[_Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """DB에 등록된 파일 행을 읽어 반환한다 (스키마 변경 없음).

    file_paths 를 지정하면 해당 절대 경로에 해당하는 행만 반환한다.
    반환 dict 키: id, file_name, file_path, ai_comment, category, tags(list[str])
    """
    if not os.path.exists(db_path):
        return []
    allowed = (
        {os.path.normcase(os.path.abspath(p)) for p in file_paths}
        if file_paths is not None
        else None
    )
    try:
        conn = _sqlite3.connect(db_path, timeout=10)
        conn.text_factory = str
        try:
            rows = conn.execute(
                "SELECT id, file_name, file_path, ai_comment, category, tags "
                "FROM files ORDER BY file_name"
            ).fetchall()
        finally:
            conn.close()
    except _sqlite3.Error:
        return []

    result: List[Dict[str, Any]] = []
    for file_id, file_name, file_path, ai_comment, category, tags_raw in rows:
        normalized = os.path.normcase(os.path.abspath(file_path or ""))
        if allowed is not None and normalized not in allowed:
            continue
        # tags 컬럼은 쉼표 구분 문자열; 비어 있으면 빈 리스트
        tag_list: List[str] = []
        if tags_raw:
            tag_list = [t.lstrip("#").strip() for t in tags_raw.split(",") if t.strip()]
        result.append({
            "id": file_id,
            "file_name": file_name or "",
            "file_path": file_path or "",
            "ai_comment": ai_comment or "",
            "category": category or "",
            "tags": tag_list,
        })
    return result


# =========================================================
# Batch 6: 증분 분석 계획 (FolderAnalysisPlanWorker에서 사용)
# =========================================================

from collections import defaultdict as _defaultdict
from pathlib import Path as _Path


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _has_analysis(ai_comment: str, category: str) -> bool:
    return bool((ai_comment or "").strip() or (category or "").strip())


def scan_directory_files_flat(
    directory: str,
    excluded_directories: _Optional[_Iterable[str]] = None,
) -> list:
    """지정 디렉터리 아래 지원 파일을 재귀 탐색해 절대 경로 목록으로 반환.

    ClasqCore.scan_directory_files()의 모듈 수준 버전.
    symlink/reparse-point는 따라가지 않는다.
    """
    from .config import SUPPORTED_EXTENSIONS
    root = _Path(directory).expanduser()
    if not root.is_dir():
        return []
    excluded = {
        name.casefold()
        for name in (excluded_directories or DEFAULT_EXCLUDED_DIRECTORIES)
    }
    files: list[str] = []
    for current_root, dirs, names in os.walk(
        root, topdown=True, onerror=lambda _: None, followlinks=False
    ):
        kept = []
        for d in dirs:
            p = _Path(current_root) / d
            try:
                attrs = getattr(os.lstat(p), "st_file_attributes", 0)
                if attrs & 0x400:  # reparse point (symlink/junction)
                    continue
            except OSError:
                continue
            if d.casefold() not in excluded:
                kept.append(d)
        dirs[:] = kept
        for name in names:
            if _Path(name).suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(str((_Path(current_root) / name).resolve()))
    return sorted(files, key=str.casefold)


def build_incremental_analysis_plan(
    file_paths: _Iterable[str],
    db_path: str = "file_manager.db",
    hash_function=None,
) -> dict:
    """stat 지문 기반으로 파일을 분류해 분석 계획(plan dict)을 반환한다.

    반환 키:
      scanned        - 처리 대상 절대 경로 리스트
      already_analyzed - 변경 없고 분석 완료된 파일
      new            - DB에 없는 신규 파일
      changed        - 내용 변경된 파일
      same_content   - 해시 일치 기존 분석 결과 재사용 가능한 파일
      incomplete     - DB에 있지만 분석 미완료이며 변경 없는 파일
      pending        - 분석이 필요한 파일 (new + changed + incomplete)
      errors         - stat/hash 오류 파일
      counts         - 각 카테고리 개수
      performance    - 내부 성능 지표
    """
    if hash_function is None:
        from .db_manager import FileRegistryManager
        hash_function = FileRegistryManager.compute_file_hash

    if not os.path.exists(db_path):
        paths = [os.path.abspath(os.path.normpath(p)) for p in file_paths]
        return {
            "scanned": paths, "already_analyzed": [], "new": paths,
            "changed": [], "same_content": [], "incomplete": [], "pending": paths,
            "errors": [], "counts": {"scanned": len(paths), "new": len(paths),
                                     "pending": len(paths)},
            "performance": {},
        }

    conn = _sqlite3.connect(db_path, timeout=30)
    try:
        rows = conn.execute(
            "SELECT file_path, file_hash, file_size, file_mtime_ns, ai_comment, category "
            "FROM files"
        ).fetchall()
        try:
            cached_rows = conn.execute(
                "SELECT file_path, file_hash, file_size, file_mtime_ns "
                "FROM file_fingerprint_cache"
            ).fetchall()
        except _sqlite3.OperationalError:
            cached_rows = []
    finally:
        conn.close()

    by_path: dict = {}
    analyzed_by_hash: dict = _defaultdict(list)
    for file_path, file_hash, file_size, file_mtime_ns, ai_comment, category in rows:
        record = {
            "file_path": file_path, "file_hash": file_hash or "",
            "file_size": file_size, "file_mtime_ns": file_mtime_ns,
            "ai_comment": ai_comment or "", "category": category or "",
            "analyzed": _has_analysis(ai_comment, category), "source": "files",
        }
        by_path[_norm(file_path)] = record
        if record["file_hash"] and record["analyzed"]:
            analyzed_by_hash[record["file_hash"]].append(record)
    for file_path, file_hash, file_size, file_mtime_ns in cached_rows:
        normalized = _norm(file_path)
        if normalized not in by_path:
            by_path[normalized] = {
                "file_path": file_path, "file_hash": file_hash or "",
                "file_size": file_size, "file_mtime_ns": file_mtime_ns,
                "ai_comment": "", "category": "", "analyzed": False, "source": "cache",
            }

    plan: dict = {
        "scanned": [], "already_analyzed": [], "new": [], "changed": [],
        "same_content": [], "incomplete": [], "pending": [], "errors": [],
    }
    perf = {
        "stat_only_skipped": 0, "sha256_calculated": 0,
        "hash_backfilled": 0, "changed_candidates": 0, "hash_errors": 0,
    }

    for raw_path in file_paths:
        file_path = os.path.abspath(os.path.normpath(raw_path))
        plan["scanned"].append(file_path)
        try:
            file_stat = os.stat(file_path)
        except OSError as exc:
            plan["errors"].append({"file_path": file_path, "error": str(exc)})
            perf["hash_errors"] += 1
            continue

        existing = by_path.get(_norm(file_path))
        fp_matches = bool(
            existing and existing["file_hash"]
            and existing["file_size"] == file_stat.st_size
            and existing["file_mtime_ns"] == file_stat.st_mtime_ns
        )
        if fp_matches:
            perf["stat_only_skipped"] += 1
            item = {
                "file_path": file_path, "file_hash": existing["file_hash"],
                "file_size": file_stat.st_size, "file_mtime_ns": file_stat.st_mtime_ns,
            }
            if existing["analyzed"]:
                plan["already_analyzed"].append(item)
            else:
                item["reason"] = "incomplete"
                plan["incomplete"].append(item)
                plan["pending"].append(item)
            continue

        if existing is not None:
            perf["changed_candidates"] += 1
        try:
            file_hash = hash_function(file_path)
            perf["sha256_calculated"] += 1
        except OSError as exc:
            plan["errors"].append({"file_path": file_path, "error": str(exc)})
            perf["hash_errors"] += 1
            continue

        item = {
            "file_path": file_path, "file_hash": file_hash,
            "file_size": file_stat.st_size, "file_mtime_ns": file_stat.st_mtime_ns,
        }

        # 해시 일치 → 내용 동일한 기존 분석 결과 재사용 가능
        if analyzed_by_hash.get(file_hash):
            source_record = analyzed_by_hash[file_hash][0]
            item["source_file_path"] = source_record["file_path"]
            plan["same_content"].append(item)
            continue

        if existing is None:
            item["reason"] = "new"
            plan["new"].append(item)
        else:
            item["reason"] = "changed"
            plan["changed"].append(item)
        plan["pending"].append(item)

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
    plan["performance"] = perf
    return plan
