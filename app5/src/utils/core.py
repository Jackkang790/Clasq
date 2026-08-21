# =========================================================
# [core.py]
# 통합 코어 모듈 - 파일 관리 시스템의 핵심 기능을 결합
# =========================================================
import os
from typing import Dict, Any, List

from .file_pipeline import TextExtractor, FileAnalyzer
from .query_parser import SearchQueryParser
from .db_manager import FileRegistryManager
from .search_engine import SearchEngine
from .config import (
    OLLAMA_URL, TEXT_MODEL, VISION_MODEL, SUPPORTED_EXTENSIONS,
)


class ClasqCore:
    """
    통합 코어 클래스
    파일 분석, DB 관리, 검색 엔진을 하나로 결합하여
    시스템의 핵심 기능을 제공합니다.
    """

    def __init__(
        self,
        db_path: str = "file_manager.db",
        ollama_url: str = OLLAMA_URL,
        text_model: str = TEXT_MODEL,
        vision_model: str = VISION_MODEL,
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

        # A. 이미지 파일 처리
        if self.extractor.is_image_file(file_path):
            img_bytes, status = self.extractor.process_image(file_path)
            if status != "SUCCESS":
                res = self.analyzer._build_fallback_response(
                    {"original_name": os.path.basename(file_path)}, status)
            else:
                res = self.analyzer.analyze_image_bytes(file_path, img_bytes)

        # B. 문서/데이터 파일 처리. 지원하지 않는 형식(음성·영상 포함)은
        # LLM에 전달하지 않고 즉시 안내한다.
        else:
            if not file_path.lower().endswith(SUPPORTED_EXTENSIONS):
                return {
                    "@TYPE": "@ERROR",
                    "status": "FAILED",
                    "message": "지원하지 않는 형식입니다. 이미지와 문서 파일만 분석할 수 있습니다.",
                }
            text, status = self.extractor.extract(file_path)
            res = self.analyzer.analyze_document_text(file_path, text)

        # 분석 결과를 DB에 저장. DB 실패도 작업 실패로 돌려 UI/워커가 알릴 수 있게 한다.
        db_result = self._save_to_db(file_path, res)
        if not db_result.get("success"):
            res["status"] = "FAILED"
            res["error"] = db_result.get("message", "분석 결과를 DB에 저장하지 못했습니다.")
        res["db_result"] = db_result

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
        """디렉터리에서 TextExtractor와 같은 지원 형식의 파일을 스캔합니다."""
        directory = self._normalize_path(directory)
        if not os.path.isdir(directory):
            return []
        files: List[Dict[str, Any]] = []
        for root, _, filenames in os.walk(directory):
            for filename in filenames:
                if filename.lower().endswith(SUPPORTED_EXTENSIONS):
                    file_path = os.path.abspath(os.path.join(root, filename))
                    files.append({"file_name": filename, "file_path": file_path,
                                  "tags": [], "category": "#미분류"})
        return files

    def get_saved_files(self) -> List[Dict[str, Any]]:
        """저장 목록 화면에 표시할 실제 분석 DB 레코드를 반환합니다."""
        return self.registry.list_files()

    def update_saved_file(self, file_id: int, display_name: str, tags: str, description: str) -> Dict[str, Any]:
        """SavedView에서 수정한 표시명·태그·설명을 저장합니다."""
        return self.registry.update_file_metadata(file_id, display_name, tags, description)

    def build_organize_preview(self, groups: Dict[str, List[Dict[str, Any]]], base_path: str) -> List[Dict[str, Any]]:
        """파일을 변경하지 않고 이동 계획과 현재 충돌 여부를 계산합니다."""
        base_path = self._normalize_path(base_path)
        preview = []
        planned_paths = set()
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
                for row in rows
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
        return {"success": bool(moved_files) or not errors,
                "message": f"파일 정리 완료: 성공 {len(moved_files)}개, 실패 {len(errors)}개",
                "moved_files": moved_files, "errors": errors}

# =========================================================
# 하위 호환성을 위한 별칭 클래스
# =========================================================
class MainProcessor(ClasqCore):
    """기존 코드와의 호환성을 위한 별칭 클래스"""
    pass
