import base64
import json
import mimetypes
import sys
import urllib.request
from pathlib import Path


API_URL = "http://127.0.0.1:8100/v1/chat/completions"
MODEL = "qwen3-vl-8b"


if len(sys.argv) != 2:
    print(r'사용법: py -3.13 test_image.py "C:\경로\이미지.png"')
    sys.exit(1)


path = Path(sys.argv[1])

if not path.exists():
    print("파일이 없습니다:", path)
    sys.exit(1)


# =========================================================
# 이미지 → Base64
# =========================================================
mime_type, _ = mimetypes.guess_type(path)
mime_type = mime_type or "image/png"

with open(path, "rb") as f:
    encoded = base64.b64encode(f.read()).decode("utf-8")

image_url = f"data:{mime_type};base64,{encoded}"


def call_qwen(payload):
    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=300) as response:
        result = json.loads(response.read().decode("utf-8"))

    return result["choices"][0]["message"]["content"]


# =========================================================
# STEP 1. OCR만 먼저 수행
# =========================================================
print("1단계: 이미지 텍스트 확인 중...")

ocr_payload = {
    "model": MODEL,
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_url
                    }
                },
                {
                    "type": "text",
                    "text": """
이미지 안에 보이는 글자를 OCR 하세요.

중요:
- 의미를 추측해서 고치지 마세요.
- 비슷하게 생긴 한글을 임의로 바꾸지 마세요.
- 글자를 확대해서 확인한다고 생각하고 최대한 정확하게 읽으세요.
- 화면에 실제로 보이는 문자열만 반환하세요.
- 설명하지 마세요.
- 따옴표도 붙이지 마세요.

예:
환자 등록
"""
                }
            ]
        }
    ],
    "temperature": 0,
    "max_tokens": 100
}

ocr_text = call_qwen(ocr_payload).strip()

print("\n===== OCR 결과 =====")
print(ocr_text)


# =========================================================
# STEP 2. OCR 결과 + 이미지로 스마트폴더 분류
# =========================================================
print("\n2단계: 파일 분류 중...")

classification_prompt = f"""
당신은 스마트 폴더 파일 분류기입니다.

현재 분석하는 파일 정보:
- 파일명: {path.name}
- 파일 형식: {path.suffix}
- 이미지에서 OCR로 읽은 텍스트: {ocr_text}

원본 이미지와 OCR 결과를 함께 참고해서
이 파일이 어떤 종류의 파일인지 판단하세요.

주의:
- OCR 결과를 임의로 다른 단어로 변경하지 마세요.
- 이미지에 보이는 내용과 파일의 용도를 함께 판단하세요.
- 단순히 OCR 텍스트를 폴더명으로 복사하지 마세요.
- 아직 사용자 폴더 구조 정보가 없으므로 suggested_folder는
  일반적인 분류 기준으로 추천하세요.
- confidence는 분류 판단에 대한 신뢰도를 0~1 사이 값으로 작성하세요.

반드시 JSON만 반환하세요.
마크다운 코드블록은 사용하지 마세요.

{{
  "ocr_text": "{ocr_text}",
  "category": "대분류",
  "sub_category": "소분류",
  "suggested_folder": "추천 폴더 경로",
  "document_type": "파일 종류",
  "summary": "내용 요약",
  "confidence": 0.0
}}
"""

classification_payload = {
    "model": MODEL,
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_url
                    }
                },
                {
                    "type": "text",
                    "text": classification_prompt
                }
            ]
        }
    ],
    "temperature": 0,
    "max_tokens": 500
}


answer = call_qwen(classification_payload)

print("\n===== Qwen3-VL 분류 결과 =====")
print(answer)