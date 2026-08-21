# =========================================================
# [query_parser.py] 
# 프론트엔드 자연어 입력 파싱 및 의도 분류 모듈 (@검색, @대화)
# =========================================================
import re          # 정규표현식 모듈
import json        # JSON 데이터 디코딩/인코딩 모듈
from datetime import date, datetime, timedelta
from ollama_manager import OllamaManager
from typing import Dict, Any
from .config import OLLAMA_URL, TEXT_MODEL, SUPPORTED_EXTENSIONS


class SearchQueryParser:
    """
    [자연어 분석 모듈] 
    사용자가 입력한 검색어/일상대화 문장을 로컬 AI(Ollama)에 전달하고,
    의도를 분석하여 구조화된 JSON(@검색, @대화)으로 변환해 반환합니다.
    """

    def __init__(self, ollama_url: str = OLLAMA_URL, model: str = TEXT_MODEL):
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model

    def parse_user_query(self, user_text: str) -> Dict[str, Any]:
        """사용자 입력 자연어를 분석하여 '@TYPE'이 포함된 JSON 객체 반환"""
        user_text = user_text.strip()
        if not user_text:
            return {"status": "SUCCESS", "data": {"@TYPE": "@검색", "query_keywords": [], "target_extension": []}, "error": None}
        inferred_date_range = self._extract_date_range(user_text)
        prompt = f"""
You are a smart Assistant for a File Management System.
Analyze the user's input string and classify the intent into either '@검색' or '@대화'.

User Input: "{user_text}"

[Classification Rules]
1. Set "@TYPE" to "@검색" IF:
   - The user wants to find, search, show, or list local files/documents/images.
   - Examples: "pdf 파일 찾아줘", "지난주 회의록 어디 있어?", "jpg 이미지 보여줘"
   - Extract key search terms into "query_keywords" (array of strings).
   - Extract file extensions if explicitly mentioned into "target_extension" (e.g., [".pdf"], [".xlsx"]).
   - Extract file extensions if explicitly mentioned into "target_extension" (e.g., [".pdf"], [".xlsx"]).
   - If a date is specified, return "date_range" as {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}.
     Use the file's modified date stored in the database. Today is {date.today().isoformat()}.

2. Set "@TYPE" to "@대화" IF:
   - The user is making casual greetings, small talk, or general questions NOT related to searching local files.
   - Examples: "안녕", "오늘 날씨 어때?", "넌 누구야?"
   - You MUST generate a polite, complete, and helpful Korean response in "reply_text".
   - DO NOT just echo or repeat the user's input! Provide a helpful real answer.

[Output Format Requirements]
Return ONLY a valid JSON object matching one of these structures:

If "@검색":
{{
  "@TYPE": "@검색",
  "query_keywords": ["keyword1", "keyword2"],
  "target_extension": [".pdf"],
  "date_range": {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}},
  "raw_query": "{user_text}"
}}

If "@대화":
{{
  "@TYPE": "@대화",
  "reply_text": "사용자 질문에 맞는 친절하고 완성도 높은 한글 대화 응답 문장"
}}

Keep your response brief and concise.
"""

        payload = {
            "model": self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 100}
        }

        try:
            res = OllamaManager.request("generate", payload, timeout=120, base_url=self.ollama_url)
            res.raise_for_status()
            
            raw_text = res.json().get("response", "").strip()

            # 응답 내 Pure JSON 영역 추출
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            json_str = match.group(0) if match else raw_text
            parsed_json = json.loads(json_str)

            # @TYPE 누락 시 기본 폴백
            if "@TYPE" not in parsed_json:
                parsed_json["@TYPE"] = "@대화"
                parsed_json["reply_text"] = raw_text if raw_text else "안녕하세요! 무엇을 도와드릴까요?"
            if parsed_json["@TYPE"] == "@검색":
                parsed_json["query_keywords"] = [str(word) for word in parsed_json.get("query_keywords", []) if str(word).strip()]
                parsed_json["target_extension"] = self._normalize_extensions(parsed_json.get("target_extension", []))
                parsed_json["date_range"] = inferred_date_range or self._normalize_date_range(parsed_json.get("date_range"))

            return {
                "status": "SUCCESS", 
                "data": parsed_json, 
                "error": None
            }

        except Exception as e:
            # AI 서버가 꺼져도 명백한 파일 검색은 계속 가능해야 한다.
            return {
                "status": "SUCCESS",
                "data": self._fallback_search(user_text),
                "error": f"Ollama 파싱 실패, 규칙 기반 검색으로 전환: {e}",
            }

    @staticmethod
    def _normalize_extensions(values: Any) -> list[str]:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return []
        supported = set(SUPPORTED_EXTENSIONS)
        return [f".{str(value).lower().lstrip('.')}" for value in values
                if f".{str(value).lower().lstrip('.')}" in supported]

    def _fallback_search(self, user_text: str) -> Dict[str, Any]:
        extensions = self._normalize_extensions(re.findall(r"\.?[A-Za-z0-9]+", user_text))
        keywords = [word for word in re.findall(r"[가-힣A-Za-z0-9_]+", user_text)
                    if f".{word.lower()}" not in extensions]
        return {"@TYPE": "@검색", "query_keywords": keywords,
                "target_extension": extensions, "date_range": self._extract_date_range(user_text),
                "raw_query": user_text}

    @staticmethod
    def _normalize_date_range(value: Any) -> Dict[str, str] | None:
        if not isinstance(value, dict):
            return None
        try:
            start = datetime.strptime(str(value.get("start", "")), "%Y-%m-%d").date()
            end = datetime.strptime(str(value.get("end", "")), "%Y-%m-%d").date()
        except ValueError:
            return None
        return {"start": start.isoformat(), "end": end.isoformat()} if start <= end else None

    @staticmethod
    def _extract_date_range(text: str) -> Dict[str, str] | None:
        today = date.today()
        normalized = re.sub(r"\s+", "", text)
        if "지난주" in normalized:
            this_monday = today - timedelta(days=today.weekday())
            start, end = this_monday - timedelta(days=7), this_monday - timedelta(days=1)
        elif "이번주" in normalized or "금주" in normalized:
            start, end = today - timedelta(days=today.weekday()), today
        elif "어제" in normalized:
            start = end = today - timedelta(days=1)
        elif "오늘" in normalized:
            start = end = today
        else:
            match = re.search(r"(20\d{2})[-.년]\s*(\d{1,2})[-.월]\s*(\d{1,2})", text)
            if not match:
                return None
            try:
                start = end = date(*map(int, match.groups()))
            except ValueError:
                return None
        return {"start": start.isoformat(), "end": end.isoformat()}


# =========================================================
# 단독 테스트 실행부 (main)
# =========================================================
if __name__ == "__main__":
    parser = SearchQueryParser(model="gemma2:9b")

    print("=== [SearchQueryParser] 자연어 의도 파싱 테스트 ===")

    res1 = parser.parse_user_query("지난주에 만든 프로젝트 보고서 pdf 파일 찾아줘")
    print("\n[테스트 1 - 검색 요청 결과]:\n", json.dumps(res1, ensure_ascii=False, indent=2))

    res2 = parser.parse_user_query("안녕, 너는 어떤 일을 할 수 있니?")
    print("\n[테스트 2 - 대화 요청 결과]:\n", json.dumps(res2, ensure_ascii=False, indent=2))
