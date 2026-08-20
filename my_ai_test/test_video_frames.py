import base64
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


# =========================================================
# 설정
# =========================================================

API_URL = "http://127.0.0.1:8100/v1/chat/completions"
MODEL = "qwen3-vl-8b"

# 장면 변화 민감도
# 낮을수록 프레임이 많이 잡힘
# 0.20 ~ 0.40 정도 추천
SCENE_THRESHOLD = 0.30

# 장면 변화가 없어도 최대 이 시간마다 한 장은 확보
MAX_GAP_SECONDS = 10

# 서버로 보낼 이미지 너비
IMAGE_WIDTH = 640

# 너무 긴 영상에서 이미지가 과도하게 많아지는 것 방지
MAX_FRAMES = 24

# Qwen 답변 최대 토큰
MAX_TOKENS = 1000


# =========================================================
# 유틸
# =========================================================

def find_ffmpeg():
    """
    PATH에서 ffmpeg를 찾고,
    없으면 우리가 설치한 C:\\ffmpeg\\bin도 확인
    """

    ffmpeg = shutil.which("ffmpeg")

    if ffmpeg:
        return ffmpeg

    default_path = Path(r"C:\ffmpeg\bin\ffmpeg.exe")

    if default_path.exists():
        return str(default_path)

    return None


def image_to_data_url(path):
    mime_type, _ = mimetypes.guess_type(path)
    mime_type = mime_type or "image/jpeg"

    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"


def format_time(seconds):
    seconds = max(0, int(round(seconds)))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{minutes:02d}:{secs:02d}"


def reduce_frames(frames, timestamps, max_frames):
    """
    프레임이 너무 많으면 영상 전체에서
    균등하게 최대 max_frames만 선택
    """

    if len(frames) <= max_frames:
        return frames, timestamps

    indices = []

    for i in range(max_frames):
        index = round(
            i * (len(frames) - 1) / (max_frames - 1)
        )
        indices.append(index)

    # 중복 index 방지
    indices = sorted(set(indices))

    new_frames = [frames[i] for i in indices]
    new_timestamps = [timestamps[i] for i in indices]

    return new_frames, new_timestamps


# =========================================================
# 입력
# =========================================================

if len(sys.argv) != 2:
    print(
        r'사용법: py -3.13 test_video_frames.py '
        r'"C:\Users\USER1\Downloads\video.mp4"'
    )
    sys.exit(1)


video_path = Path(sys.argv[1])

if not video_path.exists():
    print("파일이 없습니다:")
    print(video_path)
    sys.exit(1)


ffmpeg = find_ffmpeg()

if ffmpeg is None:
    print("FFmpeg를 찾을 수 없습니다.")
    print(r"C:\ffmpeg\bin\ffmpeg.exe 위치를 확인해주세요.")
    sys.exit(1)


# =========================================================
# 시작
# =========================================================

total_start = time.time()

original_size = video_path.stat().st_size / 1024 / 1024

print("=" * 60)
print("Qwen3-VL 스마트 동영상 분석")
print("=" * 60)

print("영상:", video_path)
print(f"원본 크기: {original_size:.2f} MB")
print(f"장면 감지 임계값: {SCENE_THRESHOLD}")
print(f"최대 프레임 간격: {MAX_GAP_SECONDS}초")
print(f"최대 전송 프레임: {MAX_FRAMES}장")


# =========================================================
# STEP 1
# 장면 변화 또는 최대 10초 간격으로 프레임 추출
# =========================================================

with tempfile.TemporaryDirectory() as temp_dir:

    temp_path = Path(temp_dir)

    output_pattern = str(
        temp_path / "frame_%04d.jpg"
    )

    print("\n[1/2] 로컬에서 중요 장면 추출 중...")

    # 핵심:
    #
    # 1. 첫 프레임 선택
    # 2. 화면 변화가 SCENE_THRESHOLD보다 크면 선택
    # 3. 화면 변화가 없어도 MAX_GAP_SECONDS가 지나면 선택
    #
    # 즉:
    # 화면 변화 → 즉시 프레임 확보
    # 정적인 화면 → 최대 10초마다 한 장

    filter_expression = (
    f"select='"
    f"isnan(prev_selected_t)"
    f"+gt(scene,{SCENE_THRESHOLD})"
    f"+gte(t-prev_selected_t,{MAX_GAP_SECONDS})"
    f"',"
    f"scale={IMAGE_WIDTH}:-2,"
    f"showinfo"
)

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(video_path),
        "-an",
        "-vf",
        filter_expression,
        "-fps_mode",
        "vfr",
        "-q:v",
        "3",
        output_pattern
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if result.returncode != 0:
        print("\nFFmpeg 처리 실패:")
        print(result.stderr[-3000:])
        sys.exit(1)

    frames = sorted(
        temp_path.glob("frame_*.jpg")
    )

    if not frames:
        print("프레임이 하나도 추출되지 않았습니다.")
        sys.exit(1)

    # -----------------------------------------------------
    # FFmpeg showinfo에서 실제 타임스탬프 추출
    # -----------------------------------------------------

    timestamps = []

    pattern = re.compile(
        r"pts_time:([\-0-9.]+)"
    )

    for line in result.stderr.splitlines():

        if "showinfo" not in line:
            continue

        match = pattern.search(line)

        if match:
            try:
                timestamps.append(
                    float(match.group(1))
                )
            except ValueError:
                pass

    # timestamp와 frame 개수가 다르면 안전하게 보정
    if len(timestamps) != len(frames):

        print(
            "타임스탬프 일부를 읽지 못해 "
            "간격 기준으로 보정합니다."
        )

        timestamps = [
            i * MAX_GAP_SECONDS
            for i in range(len(frames))
        ]

    # -----------------------------------------------------
    # 너무 많은 프레임이면 균등 축소
    # -----------------------------------------------------

    before_reduce = len(frames)

    frames, timestamps = reduce_frames(
        frames,
        timestamps,
        MAX_FRAMES
    )

    if before_reduce != len(frames):
        print(
            f"추출 프레임 {before_reduce}장 → "
            f"{len(frames)}장으로 축소"
        )

    total_frame_size = sum(
        f.stat().st_size for f in frames
    ) / 1024 / 1024

    print(f"선택된 프레임: {len(frames)}장")
    print(
        f"프레임 전체 크기: "
        f"{total_frame_size:.2f} MB"
    )

    print("\n선택된 시점:")

    for timestamp in timestamps:
        print(
            " ",
            format_time(timestamp)
        )


    # =====================================================
    # STEP 2
    # 모든 대표 프레임을 Qwen에 한 번에 전달
    # =====================================================

    print("\n[2/2] Qwen3-VL 전체 분석 요청 중...")

    content = [
        {
            "type": "text",
            "text": """
당신은 스마트 폴더에서 동영상 파일의 내용을 분석하는
비전 모델입니다.

이후 제공되는 이미지들은 하나의 동영상에서
화면 변화가 발생한 순간과 일정 시간 간격으로 추출한
대표 프레임입니다.

모든 이미지는 실제 동영상의 시간 순서대로 제공됩니다.

목표:
동영상 전체에서 사용자가 무엇을 하는지 파악하고,
나중에 파일을 검색하거나 분류할 수 있도록
간결하고 정확하게 요약하세요.

중요 규칙:

1. 실제 이미지에서 확인되는 내용만 작성하세요.

2. 화면에 없는 프로그램명, 파일명, URL,
   사용자 행동을 추측하지 마세요.

3. 텍스트가 명확하게 읽히지 않는다면
   임의로 추측하지 마세요.

4. 동일한 작업이 여러 프레임에서 계속되면
   timeline에 반복해서 작성하지 마세요.

5. 프로그램 또는 작업이 실제로 변경되는 시점을
   중심으로 timeline을 작성하세요.

6. 계산기 숫자 키패드, 메뉴 항목 등
   의미 없는 반복 UI 텍스트는 visible_text에
   나열하지 마세요.

7. visible_text에는 영상 내용을 이해하는 데
   실제로 도움이 되는 텍스트만 기록하세요.

8. applications에는 이미지에서 확실하게 확인된
   프로그램만 작성하세요.

9. 파일 분류용 suggested_folder는
   아직 사용자의 실제 폴더 구조를 모르므로
   일반적인 의미의 추천만 작성하세요.

반드시 JSON만 반환하세요.
마크다운 코드블록은 사용하지 마세요.

형식:

{
  "video_type": "영상 종류",
  "summary": "영상 전체 내용을 2~4문장으로 요약",
  "timeline": [
    {
      "time": "00:00",
      "scene": "이 시점에서 확인되는 주요 작업"
    }
  ],
  "applications": [
    "확실하게 확인된 프로그램"
  ],
  "visible_text": [
    "영상 이해에 중요한 텍스트"
  ],
  "category": "대분류",
  "sub_category": "소분류",
  "suggested_folder": "추천 폴더"
}
"""
        }
    ]

    # -----------------------------------------------------
    # 이미지 + 실제 timestamp 삽입
    # -----------------------------------------------------

    for frame, timestamp in zip(
        frames,
        timestamps
    ):

        timestamp_text = format_time(timestamp)

        content.append({
            "type": "text",
            "text": (
                f"다음 이미지는 영상 시각 "
                f"{timestamp_text}입니다."
            )
        })

        content.append({
            "type": "image_url",
            "image_url": {
                "url": image_to_data_url(frame)
            }
        })


    # =====================================================
    # API 요청
    # =====================================================

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": content
            }
        ],
        "temperature": 0,
        "max_tokens": MAX_TOKENS
    }

    data = json.dumps(
        payload,
        ensure_ascii=False
    ).encode("utf-8")

    request = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    qwen_start = time.time()

    try:

        with urllib.request.urlopen(
            request,
            timeout=900
        ) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

    except Exception as e:

        print("\nQwen 요청 실패:")
        print(type(e).__name__, e)

        print(
            "\nSSH 터널 및 Qwen API를 확인하세요:"
        )

        print(
            "curl.exe "
            "http://127.0.0.1:8100/v1/models"
        )

        sys.exit(1)

    qwen_elapsed = time.time() - qwen_start

    answer = (
        result["choices"][0]
        ["message"]["content"]
    )


# =========================================================
# 결과
# =========================================================

total_elapsed = time.time() - total_start


print("\n" + "=" * 60)
print("Qwen3-VL 최종 동영상 분석 결과")
print("=" * 60)

print(answer)

print("\n" + "=" * 60)

print(
    f"원본 동영상: "
    f"{original_size:.2f} MB"
)

print(
    f"서버 전송 이미지: "
    f"{total_frame_size:.2f} MB / "
    f"{len(frames)}장"
)

print(
    f"Qwen 분석 시간: "
    f"{qwen_elapsed:.2f}초"
)

print(
    f"전체 처리 시간: "
    f"{total_elapsed:.2f}초"
)

print("=" * 60)