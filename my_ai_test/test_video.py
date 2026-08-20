import base64
import json
import mimetypes
import sys
import urllib.request
from pathlib import Path
import time

API_URL = "http://127.0.0.1:8100/v1/chat/completions"
MODEL = "qwen3-vl-8b"

if len(sys.argv) != 2:
    print(r'사용법: py -3.13 test_video.py "C:\경로\video.mp4"')
    sys.exit(1)

path = Path(sys.argv[1])

if not path.exists():
    print("파일이 없습니다:", path)
    sys.exit(1)

mime_type, _ = mimetypes.guess_type(path)
mime_type = mime_type or "video/mp4"

size_mb = path.stat().st_size / 1024 / 1024

print("동영상:", path)
print(f"파일 크기: {size_mb:.2f} MB")

with open(path, "rb") as f:
    encoded = base64.b64encode(f.read()).decode("utf-8")

video_data = f"data:{mime_type};base64,{encoded}"

payload = {
    "model": MODEL,
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "video",
                    "video": video_data
                },
                {
                    "type": "text",
                    "text": """
이 동영상 전체를 시간 순서대로 분석하세요.

특히 다음을 지켜주세요.
- 영상에 실제로 보이지 않는 프로그램이나 작업을 추측하지 마세요.
- 짧게 나타나는 프로그램 전환도 확인하세요.
- 동일한 화면 텍스트는 한 번만 기록하세요.
- 주요 장면은 실제 발생 순서대로 작성하세요.
- 단순히 몇 프레임을 보고 영상 전체 내용을 추측하지 마세요.
- 동일하거나 거의 같은 장면은 timeline에 반복해서 작성하지 마세요.
- 화면에서 명확하게 확인할 수 없는 파일명, 프로그램명, URL은 추측하지 마세요.

반드시 아래 JSON 형식으로만 답하세요.

{
  "video_type": "영상 종류",
  "summary": "영상 전체 요약",
  "timeline": [
    {"order": 1, "scene": "첫 번째 작업"},
    {"order": 2, "scene": "두 번째 작업"}
  ],
  "visible_text": [
    "중복 제거된 중요 텍스트"
  ],
  "category": "대분류",
  "sub_category": "소분류",
  "suggested_folder": "추천 폴더",
  "confidence": 0.0
}
"""
                }
            ]
        }
    ],
    "temperature": 0,
    "max_tokens": 1000
}

data = json.dumps(payload).encode("utf-8")

request = urllib.request.Request(
    API_URL,
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST"
)

start_time = time.time()

print("\nQwen3-VL 분석 요청 중...")

try:
    with urllib.request.urlopen(request, timeout=900) as response:
        result = json.loads(response.read().decode("utf-8"))

    answer = result["choices"][0]["message"]["content"]

    print("\n===== Qwen3-VL 동영상 결과 =====")
    print(answer)
    elapsed = time.time() - start_time

    print(f"\n처리 시간: {elapsed:.2f}초")



except Exception as e:
    print("\n오류:")
    print(type(e).__name__, e)