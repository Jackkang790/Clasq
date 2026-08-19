# =========================================================
# [src/utils/search_engine.py]
# DB 검색 및 자연어 의도 라우팅 후속 로직 처리 모듈
# (불용어 제거, 동의어 사전 확장, 0건 방지 폴백 검색 완결판)
# =========================================================
import sqlite3
from typing import Dict, Any, List


class SearchEngine:
    """
    [핵심 후속 처리 엔진]
    query_parser 및 main_processor에서 넘겨받은 JSON 데이터('@TYPE')를 확인하여
    1) DB(files 테이블) 조건 조회 및 결과 테이블 반환(@검색)
    2) AI 대화 메시지 팝업 전달(@대화)
    의 실제 후속 액션을 담당하는 클래스입니다.
    """

    # 1. 자연어 검색 품질 향상을 위한 확장 불용어(Stopwords) 세트
    STOP_WORDS = {
        "파일", "문서", "폴더", "데이터", "자료", "내용", "것",
        "찾아줘", "보여줘", "검색", "알려줘", "꺼내줘", "어디있어", "어디", "있냐",
        "관련된", "관련", "에", "대한", "중 중에서", "중", "내", "속", "제일", "최근", "좀", "하나",
        "pdf", "hwp", "hwpx", "docx", "xlsx", "pptx", "png", "jpg", "jpeg", "gif", "mp3", "mp4"
    }

    # 2. 검색 정확도 극대화를 위한 동의어/유의어 매핑 사전 (🌟 '전쟁' 유의어 추가)
    SYNONYM_MAP = {
        "실습": ["실습", "현장실습", "인턴", "교육"],
        "학교": ["학교", "캠퍼스", "학사"],
        "노래": ["노래", "음원", "가사", "음악", "작업"],
        "번안": ["번안", "번역", "가사"],
        "이미지": ["이미지", "사진", "그림", "gif", "png", "jpg"],
        "보고서": ["보고서", "리포트", "과제", "기안서"],
        "회의": ["회의", "미팅", "회의록"],
        "전쟁": ["전쟁", "대전", "전투"]
    }

    def __init__(self, db_path: str = "file_manager.db"):
        """검색에 사용할 SQLite 데이터베이스 파일 경로 초기화"""
        self.db_path = db_path

    # ---------------------------------------------------------
    # [1] 자연어 파싱 결과 분기 및 액션 제어 함수
    # ---------------------------------------------------------
    def process_query_result(self, parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        '@TYPE'값(@검색, @대화, @ERROR)에 따라 UI가 실행할 액션 명령 포장
        """
        type_val = parsed_data.get("@TYPE")

        # [Case 1] 검색 -> DB 조회 후 표 데이터 갱신 명령
        if type_val in ["search", "@검색"]:

            condition = parsed_data.get("condition", {})
            if condition:
                raw_keywords = condition.get("tags", [])
            else:
                raw_keywords = parsed_data.get("query_keywords", [])

            exts = parsed_data.get("target_extension", [])

            split_keywords = []
            for kw in raw_keywords:
                split_keywords.extend(kw.split())  # 띄어쓰기 기준으로 단어 분리

            # 불용어 제거 필터링
            filtered_keywords = [
                kw.strip().lower() for kw in split_keywords
                if kw.strip() and kw.strip().lower() not in self.STOP_WORDS
            ]

            final_keywords = filtered_keywords if filtered_keywords else [
                kw.strip() for kw in split_keywords if kw.strip()]

            # 1차: 엄격한 검색 (AND) -> 안되면 2차: 완화된 검색 (OR)
            search_results, is_fallback = self.search_files_smart(
                final_keywords, exts)

            display_kw = ', '.join(final_keywords) if final_keywords else "전체"

            if is_fallback:
                msg = f"'{display_kw}' 완벽 일치 항목이 없어 연관 키워드 검색 결과 {len(search_results)}건을 보여드립니다."
            else:
                msg = f"'{display_kw}' 검색 결과 {len(search_results)}건을 찾았습니다."

            return {
                "action": "UPDATE_TABLE",
                "message": msg,
                "data": search_results
            }

        # [Case 2] @대화 -> AI 대화 응답 출력 명령
        elif type_val == "@대화":
            reply = parsed_data.get("reply_text", "안녕하세요! 무엇을 도와드릴까요?")
            return {
                "action": "SHOW_CHAT",
                "message": reply,
                "data": []
            }

        # Ⓒ [Case 3] 오류 및 예외
        else:
            return {
                "action": "ERROR",
                "message": parsed_data.get("message", "알 수 없거나 올바르지 않은 요청 타입입니다."),
                "data": []
            }

    # ---------------------------------------------------------
    # [2] 지능형 DB 검색 및 Fallback 제어 로직
    # ---------------------------------------------------------
    def search_files_smart(self, keywords: List[str], exts: List[str] = None) -> tuple[List[tuple], bool]:
        """
        1차(AND 검색) 시도 후 결과가 0건이면 2차(OR 완화 검색)로 자동 전환
        :return: (검색결과 리스트, Fallback 적용 여부)
        """
        if not keywords and not exts:
            return self._execute_sql_query([], exts, match_mode="AND"), False

        # 1차 시도: 동의어 적용 AND 조건 검색
        results = self._execute_sql_query(keywords, exts, match_mode="AND")
        if results:
            return results, False

        # 2차 시도 (Fallback): 1차에서 0건이면 OR 조건으로 완화 검색
        results_or = self._execute_sql_query(keywords, exts, match_mode="OR")
        return results_or, True

    def _execute_sql_query(self, keywords: List[str], exts: List[str] = None, match_mode: str = "AND") -> List[tuple]:
        """실제 SQLite LIKE SQL 문을 생성하고 실행하는 내부 함수"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        query = "SELECT id, file_name, file_path, ai_comment, category FROM files WHERE 1=1"
        params = []

        if keywords:
            keyword_group_sql = []

            for kw in keywords:
                if not kw.strip():
                    continue

                # 동의어 사전 매핑을 통한 검색어 확장
                synonyms = self.SYNONYM_MAP.get(kw, [kw])

                # 각 단어 또는 동의어 그룹 내에서 OR 매칭 조건 형성
                synonym_conditions = []
                for syn in synonyms:
                    synonym_conditions.append(
                        "(file_name LIKE ? OR ai_comment LIKE ? OR category LIKE ?)")
                    params.extend([f"%{syn}%", f"%{syn}%", f"%{syn}%"])

                single_kw_sql = "(" + " OR ".join(synonym_conditions) + ")"
                keyword_group_sql.append(single_kw_sql)

            if keyword_group_sql:
                # AND 모드와 OR 모드 분기 (match_mode가 OR일 경우 하나만 걸려도 매칭되도록 완화)
                join_operator = " AND " if match_mode == "AND" else " OR "
                query += " AND (" + join_operator.join(keyword_group_sql) + ")"

        # 확장자 필터 (예: .pdf, .docx 등)
        if exts:
            ext_conditions = []
            for ext in exts:
                if ext.strip():
                    ext_conditions.append("file_path LIKE ?")
                    params.append(f"%{ext}")

            if ext_conditions:
                query += " AND (" + " OR ".join(ext_conditions) + ")"

        cursor.execute(query, params)
        results = cursor.fetchall()
        conn.close()

        return results


# =========================================================
# 단독 테스트 실행부 (main)
# =========================================================
if __name__ == "__main__":
    search_engine = SearchEngine(db_path="file_manager.db")

    print("=== [SearchEngine] 스마트 검색 및 동의어 테스트 ===")

    sample_search_json = {
        "@TYPE": "@검색",
        "query_keywords": ["전쟁", "파일"],
        "target_extension": []
    }
    print("\n[검색 파싱 결과 처리]:\n",
          search_engine.process_query_result(sample_search_json))