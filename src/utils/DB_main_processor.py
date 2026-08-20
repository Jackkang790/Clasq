# ========================================================+
# [DB_main_processor.py]
# 순서도 스펙 기반 통합 라우팅, 경로 정제 및 DB 자동 저장 완결 모듈
# =========================================================
import os
from typing import Dict, Any

# 🌟 [팀원 C 수정 - 2026-08] import 방식 두 가지를 모두 지원하도록 변경
#
#   [문제 상황]
#   - gui_app.py는 "from main_processor import MainProcessor" 처럼
#     src/backend 폴더를 sys.path에 직접 넣고 평면(flat) import로 사용함
#   - workers.py는 "from src.backend.main_processor import MainProcessor" 처럼
#     프로젝트 루트를 기준으로 패키지(package) import로 사용함
#   - 이 파일이 원래처럼 "from file_pipeline import ..." (평면 import)만
#     쓰면, workers.py 방식(패키지 import)으로 실행할 때
#     "ModuleNotFoundError: No module named 'file_pipeline'" 에러가 남
#     (실제로 통합 테스트에서 재현 확인함)
#
#   [해결 방법]
#   - 먼저 상대 import(.file_pipeline)를 시도 → 패키지로 import된 경우 성공
#   - 실패하면(평면 import로 쓰이는 경우) 기존 방식으로 fallback
#   - 즉 gui_app.py / workers.py 두 실행 방식을 모두 깨지지 않게 지원
#
#   [팀장(A) 확인 필요]
#   - 최종적으로 프로젝트 진입점을 어느 방식으로 통일할지는 팀장이 정할 부분.
#     이 fallback은 "둘 다 당장은 안 깨지게" 하는 임시 방편이고,
#     진입점이 하나로 정해지면 이 try/except는 정리해도 됨.
try:
    from .file_pipeline import TextExtractor, FileAnalyzer
    from .query_parser import SearchQueryParser
    # 🌟 [팀원 C] DB 저장/무결성/중복감지 전담 모듈
    from .db_manager import FileRegistryManager
except ImportError:
    from file_pipeline import TextExtractor, FileAnalyzer
    from query_parser import SearchQueryParser
    from db_manager import FileRegistryManager  # 🌟 [팀원 C] DB 저장/무결성/중복감지 전담 모듈


class MainProcessor:
    """통합 관제탑 클래스 (DB 자동 저장 및 경로 깨짐 방어 적용)"""

    def __init__(
        self,
        extractor: TextExtractor,
        analyzer: FileAnalyzer,
        query_parser: SearchQueryParser,
        db_path: str = "file_manager.db"
    ):
        # extractor: 파일에서 텍스트/이미지를 뽑아내는 전처리 담당 (팀원 B)
        # analyzer: 뽑아낸 내용을 로컬 AI에 보내 메타데이터(JSON)로 만드는 담당 (팀원 B)
        # query_parser: 사용자가 검색창에 입력한 자연어를 의도(@검색/@대화)로 분류 (팀원 B)
        self.extractor = extractor
        self.analyzer = analyzer
        self.query_parser = query_parser
        self.db_path = db_path
        # 🌟 [변경] DB 초기화/저장/중복감지는 FileRegistryManager(팀원 C)에 전량 위임
        # - 예전에는 MainProcessor 안에 _init_db/_save_to_db 로직이 직접 있었는데,
        #   그러면 gui_app.py 쪽에도 같은 스키마가 중복 정의되는 문제가 있었음
        # - FileRegistryManager 하나로 스키마/저장/중복감지 로직을 단일화함
        self.registry = FileRegistryManager(db_path=db_path)

    def _normalize_path(self, path: str) -> str:
        """
        [경로 정제 함수]
        윈도우 환경에서 파일 경로에 원화 기호(￥)가 섞여 들어오거나,
        역슬래시(\\)와 슬래시(/)가 혼용되면서 파일을 못 찾는 버그를 방어한다.
        - '￥' -> '/' , '\\' -> '/' 로 통일한 뒤 os.path.abspath로 절대경로화
        - 이후 모든 파일 조회/저장은 이 정제된 경로 기준으로 동작한다.
        """
        if not path:
            return ""
        clean_path = path.replace('￥', '/').replace('\\', '/')
        return os.path.abspath(clean_path)

    def _save_to_db(self, file_path: str, metadata_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        [DB 저장 함수 - 팀원 C 담당 영역]
        AI 분석이 끝난 결과(metadata_result)를 DB에 반영하는 진입점.
        실제 저장/중복감지/파일이동 로직은 전부 FileRegistryManager(db_manager.py)가
        처리하고, 이 함수는 그 결과를 받아서 로그만 남기는 '얇은 래퍼(wrapper)' 역할만 한다.
        (책임을 한곳(FileRegistryManager)에 몰아둬서, 저장 로직이 여러 파일에
         중복 구현되는 걸 막기 위함)
        """
        result = self.registry.save_file_result(file_path, metadata_result)
        if not result.get("success"):
            # DB 저장 자체가 실패한 경우 (예: 파일이 저장 시점에 이미 없어짐 등)
            print(f"[DB 저장 오류]: {result.get('message')}")
        elif result.get("is_duplicate"):
            # 해시값이 같은 파일이 이미 DB에 있는 경우 -> 중복 정책(quarantine/skip/keep)에 따라 처리됨
            print(f"[중복 파일 감지]: {file_path} -> {result.get('duplicate_of')} 와 내용 동일 "
                  f"(정책: {self.registry.duplicate_policy})")
        return result

    def sync_db_with_disk(self):
        """
        [DB-디스크 동기화 함수 - 팀원 C 담당 영역]
        사용자가 탐색기에서 파일을 직접 지우거나 옮기는 등, DB가 모르는 사이에
        실제 디스크 상태가 바뀔 수 있다. 이 함수는 폴더 스캔이 끝난 뒤 호출되어
        DB에는 남아있지만 실제로는 존재하지 않는 파일 레코드를 찾아 정리(삭제)한다.
        (workers.py의 폴더 스캔 스레드 종료 시점에 호출됨)
        """
        return self.registry.sync_missing_files()

    # ---------------------------------------------------------
    # [유스케이스 1] 파일 업로드 및 분석 요청 처리
    # ---------------------------------------------------------
    def process_file_upload(self, raw_file_path: str) -> Dict[str, Any]:
        """
        [파일 1개 처리의 전체 파이프라인 - '관제탑' 역할]
        workers.py의 FolderScanAndTagWorker가 파일을 하나씩 꺼내 이 함수로 넘긴다.
        흐름: 경로 정제 -> 파일 종류 판별 -> (전처리 B) -> (AI 분석 B) -> (DB 저장 C) -> 프론트엔드 응답 포장
        """
        # 1) 경로 정제 (윈도우 경로 깨짐 방어)
        file_path = self._normalize_path(raw_file_path)

        # 2) 파일 존재 여부 확인 - 스캔 이후 파일이 지워졌거나 잘못된 경로면 에러로 라우팅
        if not os.path.exists(file_path):
            return self._route_execution({
                "@TYPE": "@ERROR",
                "message": f"파일을 찾을 수 없습니다: {file_path}"
            })

        # 3) 파일 종류별로 분기 처리 (전처리는 팀원 B의 file_pipeline.py 담당)
        # A. 이미지 파일 처리 -> Vision 모델(예: llava)로 이미지 자체를 분석
        if self.extractor.is_image_file(file_path):
            img_bytes, status = self.extractor.process_image(file_path)
            if status != "SUCCESS":
                # 이미지 리사이즈/디코딩 실패 시 AI 호출 없이 바로 실패 응답 생성
                res = self.analyzer._build_fallback_response(
                    {"original_name": os.path.basename(file_path)}, status)
            else:
                res = self.analyzer.analyze_image_bytes(file_path, img_bytes)

        # B. 오디오/비디오 미디어 파일 처리 -> STT 등으로 텍스트 추출 후 텍스트 모델로 분석
        elif self.extractor.is_media_file(file_path):
            text, status = self.extractor.process_media(file_path)
            res = self.analyzer.analyze_document_text(file_path, text)

        # C. 그 외 일반 문서(txt, pdf, docx, xlsx, hwp 등) -> 텍스트 추출 후 텍스트 모델로 분석
        else:
            text, status = self.extractor.extract(file_path)
            res = self.analyzer.analyze_document_text(file_path, text)

        # 4) 🌟 [연결의 핵심] AI 분석이 끝난 결과를 즉시 DB에 저장 (팀원 C의 registry로 위임)
        #    - 이 한 줄이 B(전처리/AI)의 결과물과 C(DB저장)를 실제로 이어주는 지점.
        self._save_to_db(file_path, res)

        # 5) 프론트엔드가 받을 응답 형태로 포장해서 반환
        return self._route_execution(res)

    # ---------------------------------------------------------
    # [유스케이스 2] 자연어 검색창 입력문 처리
    # ---------------------------------------------------------
    def process_user_query(self, user_text: str) -> Dict[str, Any]:
        """
        [자연어 검색/대화 처리]
        사용자가 검색창에 입력한 자연어(예: "회의록 파일 찾아줘")를
        query_parser(팀원 B)에게 넘겨 의도(@검색/@대화)를 판별하게 하고,
        그 결과를 프론트엔드가 이해할 수 있는 형태로 라우팅한다.
        (실제 DB 조회/검색 실행 자체는 search_engine.py가 담당하며,
         여기서는 '의도 분류' 결과만 포장해서 넘긴다)
        """
        res = self.query_parser.parse_user_query(user_text)
        return self._route_execution(res.get("data", {}))

    # # ---------------------------------------------------------
    # # [핵심 라우터] 순서도 조건 판단 및 FE 전달 데이터 포장
    # # ---------------------------------------------------------

    def _route_execution(self, json_data: Dict[str, Any]) -> Dict[str, Any]:
        type_val = json_data.get(
            "@TYPE") or json_data.get("metadata", {}).get("@TYPE")

        if type_val == "@DB":
            meta = json_data.get("metadata", {})

            formatted_payload = {
                "type": "db",
                "action": "update",
                "data": {
                    # DB 저장 후 얻은 ID (임시 0)
                    "file_id": json_data.get("file_id", 0),
                    "display_name": meta.get("display_name", ""),
                    "description": meta.get("description", ""),
                    "tags": meta.get("tags", [])
                }
            }

            db_dict = {
                "target_fe": True,
                "response_type": "FILE_ORGANIZE",
                "payload": formatted_payload
            }

            # 파일 삭제를 수행할 객체의 메서드 호출

            return db_dict

        elif type_val == "@검색":
            formatted_payload = {
                "type": "search",
                "condition": {
                    "tags": json_data.get("query_keywords", []),
                    "limit": 20
                }
            }

            search_dict = {
                "target_fe": True,
                "response_type": "SEARCH_RESULT",
                "payload": formatted_payload
            }

            # 파일 삭제를 수행할 객체의 메서드 호출

            return search_dict

        elif type_val == "@대화":
            chat_dict = {
                "target_fe": True,
                "response_type": "CHAT_RESPONSE",
                "payload": {
                    "type": "chat",
                    "message": json_data.get("reply_text", "")
                }
            }
            return chat_dict

        else:
            return {
                "target_fe": True,
                "response_type": "ERROR",
                "payload": {
                    "type": "error",
                    "message": json_data.get("message", "알 수 없는 처리 규격입니다."),
                    "raw_data": json_data
                }
            }
