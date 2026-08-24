# =========================================================
# [query_parser.py]
# 프론트엔드 자연어 입력 파싱 및 의도 분류 모듈 (@검색, @대화)
# AI_MODE=llama_server/remote : QwenClient (llama-server OpenAI-compatible API)
# AI_MODE=ollama              : 기존 Ollama API (호환 유지)
# =========================================================
import re
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict

from ollama_manager import OllamaManager
from .config import (
    OLLAMA_URL, TEXT_MODEL, SUPPORTED_EXTENSIONS, DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS, VIDEO_EXTENSIONS,
)


_TYPE_TERMS = {
    "pdf": (DOCUMENT_EXTENSIONS[:1], ("pdf",)),
    "image": (IMAGE_EXTENSIONS, ("이미지", "사진", "그림")),
    "video": (VIDEO_EXTENSIONS, ("비디오", "영상")),
    "excel": ((".xlsx",), ("엑셀", "excel", "xlsx")),
    "markdown": ((".md", ".markdown"), ("마크다운", "markdown", "md")),
    "json": ((".json",), ("json",)),
}
_INVENTORY_PATTERNS = (
    r"무슨\s*(?:파일|문서)(?:이|가)?\s*있", r"(?:파일|문서)\s*(?:뭐|무엇)\s*있",
    r"어떤\s*(?:파일|문서)\s*있", r"(?:파일|문서)\s*목록\s*(?:을\s*)?(?:보여|알려)",
    r"(?:파일|문서)\s*(?:이|가)?\s*몇\s*개", r"문서\s*종류\s*(?:가\s*)?(?:뭐|무엇|어떤)",
)
_COMMAND_FILLERS = {
    "파일", "문서", "내용", "아무거나", "아무", "거나", "있어", "있니", "있나요",
    "보여줘", "보여", "찾아줘", "찾아", "검색해줘", "검색", "알려줘", "줘",
    "오늘", "어제", "지난주", "이번주", "금주",
}


class SearchQueryParser:
    """자연어 입력을 분석하여 '@TYPE'이 포함된 JSON 객체를 반환합니다.

    AI_MODE 환경변수에 따라 llama-server(QwenClient) 또는 Ollama 중 하나를 사용합니다.
    어느 쪽도 실패하면 규칙 기반 키워드 검색으로 전환합니다(Ollama 자동 fallback 없음).
    """

    def __init__(self, ollama_url: str = OLLAMA_URL, model: str = TEXT_MODEL, **_kwargs):
        from src.ai.config import get_ai_mode
        self._mode = get_ai_mode()

        if self._mode != "ollama":
            # llama_server / remote: QwenClient 사용
            from src.ai.qwen_client import QwenClient
            self._qwen = QwenClient()
            self.model = self._qwen.config.model   # "qwen3-vl-8b"
            self.ollama_url = None                 # 사용 안 함
        else:
            # ollama: 기존 경로 유지
            self._qwen = None
            self.ollama_url = ollama_url.rstrip("/")
            self.model = model                     # "gemma3"

    # -----------------------------------------------------------------
    # 공개 API
    # -----------------------------------------------------------------

    def parse_user_query(self, user_text: str) -> Dict[str, Any]:
        """사용자 입력 자연어를 분석하여 '@TYPE'이 포함된 JSON 객체 반환."""
        if self._mode != "ollama":
            result = self._parse_with_qwen(user_text)
        else:
            result = self._parse_with_ollama(user_text)
        if result.get("status") == "SUCCESS":
            result["data"] = self._apply_intent_guards(user_text, result.get("data", {}))
        return result

    @classmethod
    def _apply_intent_guards(cls, raw_query: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
        """명확한 파일 의도와 raw-query 근거를 LLM 결과 위에 적용한다."""
        data = dict(parsed or {})
        text = (raw_query or "").strip()
        lowered = text.casefold()
        inventory = any(re.search(pattern, text) for pattern in _INVENTORY_PATTERNS)

        detected_exts: list[str] = []
        type_terms: set[str] = set()
        for _kind, (extensions, terms) in _TYPE_TERMS.items():
            if any(re.search(rf"(?<![A-Za-z0-9가-힣]){re.escape(term)}(?=$|\s|[.,!?]|에서|으로|를|을|가|이)", lowered)
                   for term in terms):
                detected_exts.extend(extensions)
                type_terms.update(term.casefold() for term in terms)

        if inventory:
            data.update({"@TYPE": "@검색", "intent": "inventory", "query_keywords": [],
                         "target_extension": [], "date_range": None})
        elif detected_exts:
            data["@TYPE"] = "@검색"
            data["intent"] = "search"
            data["target_extension"] = list(dict.fromkeys(detected_exts))
            tokens = re.findall(r"[가-힣A-Za-z0-9_]+", text)
            keywords = []
            for token in tokens:
                normalized = token.casefold()
                # 조사 결합형 type 표현(PDF에서)은 type filter로 소비한다.
                normalized = re.sub(r"(에서|으로|를|을|가|이)$", "", normalized)
                if normalized in type_terms or normalized in _COMMAND_FILLERS:
                    continue
                keywords.append(token if normalized == token.casefold() else normalized)
            data["query_keywords"] = keywords

        # raw query에 시간 표현이 없으면 모델이 만든 날짜는 신뢰하지 않는다.
        data["date_range"] = cls._extract_date_range(text)
        data["raw_query"] = text
        return data

    # -----------------------------------------------------------------
    # llama-server / remote 경로 (QwenClient)
    # -----------------------------------------------------------------

    def _parse_with_qwen(self, user_text: str) -> Dict[str, Any]:
        user_text = user_text.strip()
        if not user_text:
            return {
                "status": "SUCCESS",
                "data": {"@TYPE": "@검색", "query_keywords": [], "target_extension": []},
                "error": None,
            }

        inferred_date_range = self._extract_date_range(user_text)
        prompt = self._build_prompt(user_text)

        try:
            raw_text = self._qwen.request_text(
                prompt,
                timeout=self._qwen.config.timeout,
                max_tokens=min(400, self._qwen.config.max_tokens),
                temperature=0.1,
            )
            parsed_json = self._qwen.parse_json_content(raw_text)

            if "@TYPE" not in parsed_json:
                parsed_json["@TYPE"] = "@대화"
                parsed_json["reply_text"] = raw_text or "안녕하세요! 무엇을 도와드릴까요?"

            if parsed_json["@TYPE"] == "@검색":
                parsed_json["query_keywords"] = [
                    str(w) for w in parsed_json.get("query_keywords", []) if str(w).strip()
                ]
                parsed_json["target_extension"] = self._normalize_extensions(
                    parsed_json.get("target_extension", [])
                )
                parsed_json["date_range"] = (
                    inferred_date_range
                    or self._normalize_date_range(parsed_json.get("date_range"))
                )

            return {"status": "SUCCESS", "data": parsed_json, "error": None}

        except Exception as exc:
            # AI 실패 → 규칙 기반 키워드 검색 (Ollama 자동 전환 없음)
            return {
                "status": "SUCCESS",
                "data": self._fallback_search(user_text),
                "error": f"AI 파싱 실패, 규칙 기반 검색으로 전환: {exc}",
            }

    # -----------------------------------------------------------------
    # Ollama 경로 (기존 코드 그대로 유지)
    # -----------------------------------------------------------------

    def _parse_with_ollama(self, user_text: str) -> Dict[str, Any]:
        user_text = user_text.strip()
        if not user_text:
            return {
                "status": "SUCCESS",
                "data": {"@TYPE": "@검색", "query_keywords": [], "target_extension": []},
                "error": None,
            }
        inferred_date_range = self._extract_date_range(user_text)
        prompt = self._build_prompt(user_text)

        payload = {
            "model": self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 100},
        }

        try:
            res = OllamaManager.request("generate", payload, timeout=120, base_url=self.ollama_url)
            res.raise_for_status()

            raw_text = res.json().get("response", "").strip()

            match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            json_str = match.group(0) if match else raw_text
            parsed_json = json.loads(json_str)

            if "@TYPE" not in parsed_json:
                parsed_json["@TYPE"] = "@대화"
                parsed_json["reply_text"] = raw_text if raw_text else "안녕하세요! 무엇을 도와드릴까요?"
            if parsed_json["@TYPE"] == "@검색":
                parsed_json["query_keywords"] = [
                    str(word) for word in parsed_json.get("query_keywords", []) if str(word).strip()
                ]
                parsed_json["target_extension"] = self._normalize_extensions(
                    parsed_json.get("target_extension", [])
                )
                parsed_json["date_range"] = (
                    inferred_date_range
                    or self._normalize_date_range(parsed_json.get("date_range"))
                )

            return {"status": "SUCCESS", "data": parsed_json, "error": None}

        except Exception as exc:
            return {
                "status": "SUCCESS",
                "data": self._fallback_search(user_text),
                "error": f"Ollama 파싱 실패, 규칙 기반 검색으로 전환: {exc}",
            }

    # -----------------------------------------------------------------
    # 공통 프롬프트 빌더
    # -----------------------------------------------------------------

    @staticmethod
    def _build_prompt(user_text: str) -> str:
        return f"""
You are a smart Assistant for a File Management System.
Analyze the user's input string and classify the intent into either '@검색' or '@대화'.

User Input: "{user_text}"

[Classification Rules]
1. Set "@TYPE" to "@검색" IF:
   - The user wants to find, search, show, or list local files/documents/images.
   - Examples: "pdf 파일 찾아줘", "지난주 회의록 어디 있어?", "jpg 이미지 보여줘"
   - Extract key search terms into "query_keywords" (array of strings).
   - Treat tag names (including "일반 태그") as search terms. Do not omit them merely because they describe a tag.
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
""".strip()

    # -----------------------------------------------------------------
    # 공통 헬퍼 (기존 코드 그대로 유지)
    # -----------------------------------------------------------------

    @staticmethod
    def _normalize_extensions(values: Any) -> list:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return []
        supported = set(SUPPORTED_EXTENSIONS)
        return [
            f".{str(v).lower().lstrip('.')}"
            for v in values
            if f".{str(v).lower().lstrip('.')}" in supported
        ]

    def _fallback_search(self, user_text: str) -> Dict[str, Any]:
        extensions = self._normalize_extensions(re.findall(r"\.?[A-Za-z0-9]+", user_text))
        keywords = [
            word for word in re.findall(r"[가-힣A-Za-z0-9_]+", user_text)
            if f".{word.lower()}" not in extensions
        ]
        return {
            "@TYPE": "@검색",
            "query_keywords": keywords,
            "target_extension": extensions,
            "date_range": self._extract_date_range(user_text),
            "raw_query": user_text,
        }

    @staticmethod
    def _normalize_date_range(value: Any):
        if not isinstance(value, dict):
            return None
        try:
            start = datetime.strptime(str(value.get("start", "")), "%Y-%m-%d").date()
            end = datetime.strptime(str(value.get("end", "")), "%Y-%m-%d").date()
        except ValueError:
            return None
        return {"start": start.isoformat(), "end": end.isoformat()} if start <= end else None

    @staticmethod
    def _extract_date_range(text: str):
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
