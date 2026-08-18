# =========================================================
# [main_processor.py]
# 순서도 스펙 기반 통합 라우팅, 경로 정제 및 DB 자동 저장 완결 모듈
# =========================================================
import os
from typing import Dict, Any

# [팀원 C 수정 적용] 실행 환경(패키지 vs 평면)에 따른 Import 충돌 방지
try:
    from .file_pipeline import TextExtractor, FileAnalyzer
    from .query_parser import SearchQueryParser
    from .db_manager import FileRegistryManager  # DB 관리는 전적으로 위임
except ImportError:
    from file_pipeline import TextExtractor, FileAnalyzer
    from query_parser import SearchQueryParser
    from db_manager import FileRegistryManager


class MainProcessor:
    """통합 관제탑 클래스 (DB 자동 저장 및 경로 깨짐 방어 적용)"""

    def __init__(
        self,
        extractor: TextExtractor,
        analyzer: FileAnalyzer,
        query_parser: SearchQueryParser,
        db_path: str = "file_manager.db"
    ):
        self.extractor = extractor
        self.analyzer = analyzer
        self.query_parser = query_parser
        self.db_path = db_path

        # 기존에 MainProcessor에 있던 _init_db 등의 중복 코드를 삭제하고,
        # 팀원 C가 만든 FileRegistryManager 객체 생성 하나로 깔끔하게 단일화
        self.registry = FileRegistryManager(db_path=db_path)

    def _normalize_path(self, path: str) -> str:
        """경로 문자열의 ￥ 기호 및 슬래시 깨짐 방어"""
        if not path:
            return ""
        clean_path = path.replace('￥', '/').replace('\\', '/')
        return os.path.abspath(clean_path)

    def _save_to_db(self, file_path: str, metadata_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        AI 분석 완료 후 SQLite DB에 자동 저장 및 즉시 커넥션 종결
        실제 복잡한 저장 로직, 중복 검사는 db_manager.py로 위임됨
        """
        result = self.registry.save_file_result(file_path, metadata_result)
        if not result.get("success"):
            print(f"[DB 저장 오류]: {result.get('message')}")
        elif result.get("is_duplicate"):
            print(f"[중복 파일 감지]: {file_path} -> {result.get('duplicate_of')} 와 내용 동일 "
                  f"(정책: {self.registry.duplicate_policy})")
        return result

    def sync_db_with_disk(self):
        """DB에는 있지만 실제 디스크에는 없는 파일 동기화 삭제 (워커 종료 시점 활용)"""
        return self.registry.sync_missing_files()

    # ---------------------------------------------------------
    # [유스케이스 1] 파일 업로드 및 분석 요청 처리
    # ---------------------------------------------------------
    def process_file_upload(self, raw_file_path: str) -> Dict[str, Any]:
        file_path = self._normalize_path(raw_file_path)

        if not os.path.exists(file_path):
            return self._route_execution({
                "@TYPE": "@ERROR",
                "message": f"파일을 찾을 수 없습니다: {file_path}"
            })

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

        # 분석 결과를 DB에 즉시 저장 (registry 호출)
        self._save_to_db(file_path, res)

        return self._route_execution(res)

    # ---------------------------------------------------------
    # [유스케이스 2] 자연어 검색창 입력문 처리
    # ---------------------------------------------------------
    def process_user_query(self, user_text: str) -> Dict[str, Any]:
        res = self.query_parser.parse_user_query(user_text)
        return self._route_execution(res.get("data", {}))
