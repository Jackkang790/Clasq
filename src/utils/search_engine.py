# =========================================================
# [search_engine.py]
# DB 검색 및 자연어 의도 라우팅 후속 로직 처리 모듈
# (불용어 제거, 동의어 사전 확장, 0건 방지 폴백 검색 완결판)
# =========================================================
import sqlite3
from typing import Dict, Any, List

from .search_normalization import normalize_query_token
from .search_aliases import build_search_alias_map, equivalent_terms


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
        "찾아줘", "보여줘", "검색", "알려줘", "꺼내줘", "어디있어", "어디", "있냐", "태그",
        "관련된", "관련", "에", "대한", "중 중에서", "중", "내", "속", "제일", "최근", "좀", "하나",
        "pdf", "hwp", "hwpx", "docx", "xlsx", "pptx", "txt", "csv", "json", "xml", "yaml", "yml", "html", "htm", "md", "markdown",
        "png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif", "mp3", "mp4"
    }

    # 2. 검색 정확도 극대화를 위한 동의어/유의어 매핑 사전
    SYNONYM_MAP = {
        "실습": ["실습", "현장실습", "인턴", "교육"],
        "학교": ["학교", "캠퍼스", "학사"],
        "노래": ["노래", "음원", "가사", "음악", "작업"],
        "번안": ["번안", "번역", "가사"],
        "번역": ["번역", "번역문"],
        "이미지": ["이미지", "사진", "그림", "gif", "png", "jpg"],
        "보고서": ["보고서", "리포트", "과제", "기안서"],
        "회의": ["회의", "미팅", "회의록"],
        "전쟁": ["전쟁", "대전", "전투"],
        "졸업": ["졸업", "수료", "학위"],
    }

    def __init__(self, db_path: str = "file_manager.db",
                 result_limit: int = 200,
                 project_aliases: dict | None = None):
        """검색에 사용할 SQLite 데이터베이스 파일 경로 초기화"""
        self.db_path = db_path
        self.result_limit = max(1, int(result_limit))
        self.search_aliases = build_search_alias_map(project_aliases)

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
            date_range = parsed_data.get("date_range")

            split_keywords = []
            for kw in raw_keywords:
                split_keywords.extend(kw.lstrip("#").split())  # 띄어쓰기 기준으로 단어 분리

            # 정규화 + 불용어 제거 (조사 제거, CamelCase/구분자 처리 포함)
            filtered_keywords = [
                normalize_query_token(kw)
                for kw in split_keywords
                if normalize_query_token(kw) and normalize_query_token(kw) not in self.STOP_WORDS
            ]

            final_keywords = filtered_keywords if filtered_keywords else [
                kw.strip() for kw in split_keywords if kw.strip()]

            # 1차: 엄격한 검색 (AND) -> 안되면 2차: 완화된 검색 (OR)
            try:
                search_results, is_fallback = self.search_files_smart(final_keywords, exts, date_range)
            except sqlite3.Error as exc:
                return {"action": "ERROR", "message": f"검색 DB 오류: {exc}", "data": []}

            display_kw = ', '.join(final_keywords) if final_keywords else "전체"
            date_label = self._date_range_label(date_range)

            if is_fallback:
                msg = f"'{display_kw}'{date_label} 완벽 일치 항목이 없어 연관 키워드 검색 결과 {len(search_results)}건을 보여드립니다."
            else:
                msg = f"'{display_kw}'{date_label} 검색 결과 {len(search_results)}건을 찾았습니다."

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
    def search_files_smart(self, keywords: List[str], exts: List[str] = None,
                           date_range: Dict[str, str] | None = None) -> tuple[List[tuple], bool]:
        """
        1차(AND 검색) 시도 후 결과가 0건이면 2차(OR 완화 검색)로 자동 전환
        :return: (검색결과 리스트, Fallback 적용 여부)
        """
        if not keywords and not exts:
            return self._execute_sql_query([], exts, date_range, match_mode="AND"), False

        # 1차 시도: 동의어 적용 AND 조건 검색
        results = self._execute_sql_query(keywords, exts, date_range, match_mode="AND")
        if results:
            return results, False

        # 2차 시도 (Fallback): 1차에서 0건이면 OR 조건으로 완화 검색
        results_or = self._execute_sql_query(keywords, exts, date_range, match_mode="OR")
        return results_or, True

    def _execute_sql_query(self, keywords: List[str], exts: List[str] = None,
                           date_range: Dict[str, str] | None = None, match_mode: str = "AND") -> List[tuple]:
        """실제 SQLite LIKE SQL 문을 생성하고 실행하는 내부 함수."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        # tags 컬럼은 DB 버전에 따라 없을 수 있으므로 제외 (UI는 *_ 언패킹으로 호환)
        query = "SELECT id, file_name, file_path, ai_comment, category FROM files WHERE 1=1"
        params = []

        if keywords:
            keyword_group_sql = []

            for kw in keywords:
                if not kw.strip():
                    continue

                # 동의어 사전 + 별칭 확장으로 검색어 보강
                base_synonyms = self.SYNONYM_MAP.get(kw, [kw])
                all_terms: list[str] = []
                for base in base_synonyms:
                    all_terms.extend(equivalent_terms(base, self.search_aliases))
                synonyms = list(dict.fromkeys(all_terms))  # 중복 제거, 순서 유지

                # 각 단어 또는 동의어 그룹 내에서 OR 매칭 조건 형성
                synonym_conditions = []
                for syn in synonyms:
                    synonym_conditions.append(
                        "(file_name LIKE ? OR ai_comment LIKE ? OR category LIKE ?)")
                    params.extend([f"%{syn}%"] * 3)

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

        # DB 스키마가 버전에 따라 다를 수 있으므로 사용 가능한 컬럼을 1회 확인
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(files)").fetchall()}

        if isinstance(date_range, dict) and date_range.get("start") and date_range.get("end"):
            if "file_modified_at" in existing_cols:
                date_col = "COALESCE(file_modified_at, updated_at, created_at)"
            elif "file_mtime_ns" in existing_cols:
                # file_mtime_ns는 나노초 정수이므로 날짜 비교 불가 → updated_at 사용
                date_col = "COALESCE(updated_at, created_at)"
            else:
                date_col = "COALESCE(updated_at, created_at)"
            query += f" AND date({date_col}) BETWEEN date(?) AND date(?)"
            params.extend([date_range["start"], date_range["end"]])
        if "file_modified_at" in existing_cols:
            query += " ORDER BY file_modified_at DESC, updated_at DESC, file_name COLLATE NOCASE"
        elif "file_mtime_ns" in existing_cols:
            query += " ORDER BY file_mtime_ns DESC, updated_at DESC, file_name COLLATE NOCASE"
        else:
            query += " ORDER BY updated_at DESC, file_name COLLATE NOCASE"
        query += f" LIMIT {self.result_limit}"
        cursor.execute(query, params)
        results = cursor.fetchall()
        conn.close()

        return results

    def invalidate_snapshot(self) -> None:
        """DB 변경 후 search_snapshot 캐시를 무효화한다 (다음 검색 시 재빌드)."""
        from .search_snapshot import invalidate_search_snapshot
        invalidate_search_snapshot(self.db_path)

    def refresh_snapshot(self):
        """search_snapshot 캐시를 즉시 재빌드하여 반환한다."""
        from .search_snapshot import refresh_search_snapshot
        return refresh_search_snapshot(self.db_path)

    @staticmethod
    def _date_range_label(date_range: Dict[str, str] | None) -> str:
        if not isinstance(date_range, dict) or not date_range.get("start"):
            return ""
        if date_range["start"] == date_range.get("end"):
            return f" ({date_range['start']})"
        return f" ({date_range['start']}~{date_range.get('end')})"


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
