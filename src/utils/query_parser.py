# =========================================================
# [query_parser.py] 
# 프론트엔드 자연어 입력 파싱 및 의도 분류 모듈 (@검색, @대화)
# =========================================================
import re          # 정규표현식 모듈
import json        # JSON 데이터 디코딩/인코딩 모듈
import requests    # 로컬 AI(Ollama) HTTP API 통신 모듈
from typing import Dict, Any


class SearchQueryParser:
    """
    [자연어 분석 모듈] 
    사용자가 입력한 검색어/일상대화 문장을 로컬 AI(Ollama)에 전달하고,
    의도를 분석하여 구조화된 JSON(@검색, @대화)으로 변환해 반환합니다.
    """

    def __init__(self, ollama_url: str = "http://localhost:11434", model: str = "qwen2.5:3b"):
        self.ollama_api_url = f"{ollama_url.rstrip('/')}/api/generate"
        self.model = model

    def parse_user_query(self, user_text: str) -> Dict[str, Any]:
        """사용자 입력 자연어를 분석하여 '@TYPE'이 포함된 JSON 객체 반환"""
        
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
  "raw_query": "{user_text}"
}}

If "@대화":
{{
  "@TYPE": "@대화",
  "reply_text": "사용자 질문에 맞는 친절하고 완성도 높은 한글 대화 응답 문장"
}}
"""

        payload = {
            "model": self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1}
        }

        try:
            res = requests.post(self.ollama_api_url, json=payload, timeout=30)
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

            return {
                "status": "SUCCESS", 
                "data": parsed_json, 
                "error": None
            }

        except Exception as e:
            return {
                "status": "FAILED",
                "data": {
                    "@TYPE": "@ERROR", 
                    "message": f"자연어 파싱 처리 중 오류 발생: {str(e)}"
                },
                "error": str(e)
            }


# =========================================================
# 단독 테스트 실행부 (main)
# =========================================================
if __name__ == "__main__":
    parser = SearchQueryParser(model="qwen2.5:3b")

    print("=== [SearchQueryParser] 자연어 의도 파싱 테스트 ===")

    res1 = parser.parse_user_query("지난주에 만든 프로젝트 보고서 pdf 파일 찾아줘")
    print("\n[테스트 1 - 검색 요청 결과]:\n", json.dumps(res1, ensure_ascii=False, indent=2))

    res2 = parser.parse_user_query("안녕, 너는 어떤 일을 할 수 있니?")
    print("\n[테스트 2 - 대화 요청 결과]:\n", json.dumps(res2, ensure_ascii=False, indent=2))