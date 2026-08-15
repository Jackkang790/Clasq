# =========================================================
# [main_processor.py] 
# 순서도 스펙 기반 통합 라우팅, 경로 정제 및 DB 자동 저장 완결 모듈
# =========================================================
import os
import sqlite3
from typing import Dict, Any

from file_pipeline import TextExtractor, FileAnalyzer
from query_parser import SearchQueryParser


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
        self._init_db()

    def _init_db(self):
        """DB 테이블 존재 여부 확인 및 자동 생성"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT,
                file_path TEXT UNIQUE,
                ai_comment TEXT,
                category TEXT
            )
        ''')
        conn.commit()
        conn.close()

    def _normalize_path(self, path: str) -> str:
        """경로 문자열의 ￥ 기호 및 슬래시 깨짐 방어"""
        if not path:
            return ""
        clean_path = path.replace('￥', '/').replace('\\', '/')
        return os.path.abspath(clean_path)

    def _save_to_db(self, file_path: str, metadata_result: Dict[str, Any]):
        """AI 분석 완료 후 SQLite DB에 자동 저장 및 즉시 커넥션 종결"""
        try:
            meta = metadata_result.get("metadata", {})
            file_name = os.path.basename(file_path)
            ai_comment = meta.get("ai_comment", "")
            tags = meta.get("tags", [])
            category = f"#{tags[0]}" if tags else "#일반"

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO files (file_name, file_path, ai_comment, category)
                VALUES (?, ?, ?, ?)
            ''', (file_name, file_path, ai_comment, category))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[DB 저장 오류]: {e}")

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
                res = self.analyzer._build_fallback_response({"original_name": os.path.basename(file_path)}, status)
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

        # 🌟 [연결의 핵심] 분석 결과를 DB에 즉시 저장!
        self._save_to_db(file_path, res)

        return self._route_execution(res)

    # ---------------------------------------------------------
    # [유스케이스 2] 자연어 검색창 입력문 처리
    # ---------------------------------------------------------
    def process_user_query(self, user_text: str) -> Dict[str, Any]:
        res = self.query_parser.parse_user_query(user_text)
        return self._route_execution(res.get("data", {}))

    # ---------------------------------------------------------
    # [핵심 라우터] 순서도 조건 판단 및 FE 전달 데이터 포장
    # ---------------------------------------------------------
    def _route_execution(self, json_data: Dict[str, Any]) -> Dict[str, Any]:
        type_val = json_data.get("@TYPE") or json_data.get("metadata", {}).get("@TYPE")

        if type_val == "@DB":
            return {
                "target_fe": True, 
                "response_type": "FILE_ORGANIZE", 
                "payload": json_data
            }
        elif type_val == "@검색":
            return {
                "target_fe": True, 
                "response_type": "SEARCH_RESULT", 
                "payload": json_data
            }
        elif type_val == "@대화":
            return {
                "target_fe": True, 
                "response_type": "CHAT_RESPONSE", 
                "payload": json_data
            }
        else:
            return {
                "target_fe": True, 
                "response_type": "ERROR", 
                "payload": {
                    "@TYPE": "@ERROR",
                    "message": json_data.get("message", "알 수 없는 처리 규격입니다."),
                    "raw_data": json_data
                }
            }