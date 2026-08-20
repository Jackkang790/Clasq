# =========================================================
# [main_processor.py]
# 순서도 스펙 기반 통합 라우팅, 경로 정제 및 DB 자동 저장 완결 모듈
# =========================================================
import os
import threading
from dataclasses import dataclass
from typing import Dict, Any, Optional

# [팀원 C 수정 적용] 실행 환경(패키지 vs 평면)에 따른 Import 충돌 방지
try:
    from .file_pipeline import TextExtractor, FileAnalyzer
    from .query_parser import SearchQueryParser
    from .db_manager import FileRegistryManager  # DB 관리는 전적으로 위임
except ImportError:
    from file_pipeline import TextExtractor, FileAnalyzer
    from query_parser import SearchQueryParser
    from db_manager import FileRegistryManager


_WHISPER_EXTRACTION_LOCK = threading.Lock()


@dataclass(frozen=True)
class FileFingerprint:
    file_size: int
    file_mtime_ns: int
    file_hash: Optional[str] = None


@dataclass(frozen=True)
class AnalysisResult:
    file_path: str
    metadata_result: Dict[str, Any]


class MainProcessor:
    """통합 관제탑 클래스 (DB 자동 저장 및 경로 깨짐 방어 적용)"""

    VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi')

    def __init__(
        self,
        extractor: TextExtractor,
        analyzer: FileAnalyzer,
        query_parser: SearchQueryParser,
        db_path: str = "file_manager.db",
        initialize_registry: bool = True,
    ):
        self.extractor = extractor
        self.analyzer = analyzer
        self.query_parser = query_parser
        self.db_path = db_path

        # 기존에 MainProcessor에 있던 _init_db 등의 중복 코드를 삭제하고,
        # 팀원 C가 만든 FileRegistryManager 객체 생성 하나로 깔끔하게 단일화
        self.registry = FileRegistryManager(db_path=db_path) if initialize_registry else None

    @staticmethod
    def capture_fingerprint(file_path: str, file_hash: Optional[str] = None) -> FileFingerprint:
        file_stat = os.stat(file_path)
        return FileFingerprint(file_stat.st_size, file_stat.st_mtime_ns, file_hash)

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
        if self.registry is None:
            raise RuntimeError("This analysis-only MainProcessor has no DB registry.")
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
    def analyze_file(
        self,
        raw_file_path: str,
        expected_fingerprint: Optional[FileFingerprint] = None,
    ) -> AnalysisResult:
        """Parse and run AI inference without DB writes or physical file moves."""
        file_path = self._normalize_path(raw_file_path)

        if not os.path.exists(file_path):
            return AnalysisResult(file_path, {
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

        # B. 비디오는 대표 프레임 분석, 오디오는 기존 Whisper STT 유지
        elif self.extractor.is_media_file(file_path):
            if os.path.splitext(file_path)[1].lower() in self.VIDEO_EXTENSIONS:
                res = self.analyzer.analyze_video(file_path)
            else:
                # Independent task extractors may each own a lazy Whisper model,
                # but model loading/transcription remains process-serial.
                with _WHISPER_EXTRACTION_LOCK:
                    text, status = self.extractor.process_media(file_path)
                if status != "SUCCESS":
                    res = self.analyzer._build_fallback_response(
                        {"original_name": os.path.basename(file_path)}, status)
                else:
                    res = self.analyzer.analyze_document_text(file_path, text)

        # C. 일반 문서/데이터 파일 처리
        else:
            text, status = self.extractor.extract(file_path)
            if status != "SUCCESS":
                res = self.analyzer._build_fallback_response(
                    {"original_name": os.path.basename(file_path)}, status)
            else:
                res = self.analyzer.analyze_document_text(file_path, text)

        return AnalysisResult(file_path, res)

    def save_analyzed_result(
        self,
        raw_file_path: str,
        analysis_result: AnalysisResult,
        expected_fingerprint: FileFingerprint,
    ) -> Dict[str, Any]:
        """Validate the source fingerprint, then serialize DB/duplicate effects."""
        file_path = self._normalize_path(raw_file_path)
        try:
            current = self.capture_fingerprint(file_path, expected_fingerprint.file_hash)
        except OSError as exc:
            return {
                "target_fe": True,
                "response_type": "ERROR",
                "payload": {"data": {
                    "status": "FAILED", "error": str(exc), "stale": True,
                    "reason": "changed_during_analysis",
                }},
            }
        if (current.file_size != expected_fingerprint.file_size
                or current.file_mtime_ns != expected_fingerprint.file_mtime_ns):
            return {
                "target_fe": True,
                "response_type": "ERROR",
                "payload": {"data": {
                    "status": "FAILED",
                    "error": "changed_during_analysis",
                    "stale": True,
                    "reason": "changed_during_analysis",
                }},
            }

        raw_result = analysis_result.metadata_result
        if raw_result.get("@TYPE") == "@ERROR":
            return self._route_execution(raw_result)
        storage_result = self._save_to_db(file_path, raw_result)
        if not storage_result.get("success"):
            return {
                "target_fe": True,
                "response_type": "ERROR",
                "payload": {"data": {
                    "status": "FAILED",
                    "error": storage_result.get("message", "DB save failed"),
                    "stale": False,
                }},
            }
        return self._route_execution(raw_result)

    def process_file_upload(self, raw_file_path: str) -> Dict[str, Any]:
        """Backward-compatible sequential analyze + validated save facade."""
        file_path = self._normalize_path(raw_file_path)
        try:
            fingerprint = self.capture_fingerprint(file_path)
        except OSError:
            return self._route_execution({
                "@TYPE": "@ERROR", "message": f"파일을 찾을 수 없습니다: {file_path}"
            })
        result = self.analyze_file(file_path, fingerprint)
        return self.save_analyzed_result(file_path, result, fingerprint)

    # ---------------------------------------------------------
    # [유스케이스 2] 자연어 검색창 입력문 처리
    # ---------------------------------------------------------
    def process_user_query(self, user_text: str) -> Dict[str, Any]:
        res = self.query_parser.parse_user_query(user_text)
        return self._route_execution(res.get("data", {}))

    def _route_execution(self, json_data: Dict[str, Any]) -> Dict[str, Any]:
        """기존 @TYPE 규격을 FrontEnd 명령 형식으로 변환한다."""
        type_val = json_data.get("@TYPE") or json_data.get("metadata", {}).get("@TYPE")

        if type_val == "@DB":
            meta = json_data.get("metadata", {})
            return {
                "target_fe": True, "response_type": "FILE_ORGANIZE",
                "payload": {
                    "type": "db", "action": "update",
                    "data": {
                        "file_id": json_data.get("file_id", 0),
                        "status": json_data.get("status", "SUCCESS"),
                        "error": json_data.get("error"),
                        "file_info": json_data.get("file_info", {}),
                        "display_name": meta.get("display_name", ""),
                        "description": meta.get("description", ""),
                        "tags": meta.get("tags", []),
                        "metadata": meta,
                    },
                },
            }
        if type_val == "@검색":
            return {
                "target_fe": True, "response_type": "SEARCH_RESULT",
                "payload": {
                    "type": "search",
                    "condition": {"tags": json_data.get("query_keywords", []), "limit": 20},
                },
            }
        if type_val == "@대화":
            return {
                "target_fe": True, "response_type": "CHAT_RESPONSE",
                "payload": {"type": "chat", "message": json_data.get("reply_text", "")},
            }
        return {
            "target_fe": True, "response_type": "ERROR",
            "payload": {
                "type": "error",
                "message": json_data.get("message", "알 수 없는 처리 규격입니다."),
                "raw_data": json_data,
            },
        }
