# =========================================================
# [core.py]
# 통합 코어 모듈 - 파일 관리 시스템의 핵심 기능을 결합
# =========================================================
import os
from typing import Dict, Any

from .file_pipeline import TextExtractor, FileAnalyzer
from .query_parser import SearchQueryParser
from .db_manager import FileRegistryManager
from .search_engine import SearchEngine


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
        whisper_model: str = "base"
    ):
        """
        코어 시스템 초기화
        
        Args:
            db_path: SQLite DB 파일 경로
            ollama_url: Ollama API URL
            text_model: 텍스트 분석용 모델
            vision_model: 이미지 분석용 모델
            whisper_model: 음성 인식용 모델
        """
        self.db_path = db_path
        
        # DB 관리자 초기화
        self.registry = FileRegistryManager(db_path=db_path)
        
        # 파일 파이프라인 초기화
        self.extractor = TextExtractor(whisper_model_name=whisper_model)
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

        # B. 오디오/비디오 미디어 파일 처리
        elif self.extractor.is_media_file(file_path):
            text, status = self.extractor.process_media(file_path)
            res = self.analyzer.analyze_document_text(file_path, text)

        # C. 일반 문서/데이터 파일 처리
        else:
            text, status = self.extractor.extract(file_path)
            res = self.analyzer.analyze_document_text(file_path, text)

        # 분석 결과를 DB에 저장
        self._save_to_db(file_path, res)

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

        valid_extensions = (
            '.txt', '.pdf', '.docx', '.xlsx', '.pptx', '.hwp', '.hwpx',
            '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif',
            '.mp3', '.mp4', '.wav', '.m4a', '.mkv', '.avi'
        )
        
        files_to_process = []
        
        for root, _, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(valid_extensions):
                    full_path = os.path.join(root, file)
                    clean_path = os.path.abspath(os.path.normpath(
                        full_path.replace('￥', '/').replace('\\', '/')))
                    files_to_process.append(clean_path)
        
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
                    self.process_file_upload(file_path)
                    success_count += 1
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


# =========================================================
# 하위 호환성을 위한 별칭 클래스
# =========================================================
class MainProcessor(ClasqCore):
    """기존 코드와의 호환성을 위한 별칭 클래스"""
    pass