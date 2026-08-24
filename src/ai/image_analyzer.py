"""Two-pass Qwen vision analysis based on my_ai_test/test_image.py."""

from __future__ import annotations

import base64
import io
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image

from .image_color import extract_dominant_colors
from .qwen_client import AIClientError, QwenClient


OCR_PROMPT = """
이미지 안에 실제로 보이는 모든 글자를 정확히 그대로 옮겨 적으세요.

규칙:
- 문맥이나 의미로 철자를 보정하지 마세요.
- 비슷하게 생긴 한글을 임의로 바꾸지 마세요.
- 각 글자의 자모 형태를 확대해서 확인하세요.
- 보이는 기호도 생략하지 마세요.
- 설명, 따옴표, 마크다운 없이 보이는 문자열만 반환하세요.
""".strip()


class ImageAnalyzer:
    def __init__(self, client: Optional[QwenClient] = None) -> None:
        self.client = client or QwenClient()

    # =========================================================
    # 파일 기본 정보
    # =========================================================

    @staticmethod
    def _file_info(file_path: str) -> Dict[str, Any]:
        path = Path(file_path)

        return {
            "original_name": path.name,
            "file_extension": path.suffix.lower(),
            "file_size_bytes": path.stat().st_size if path.exists() else 0,
            "analyzed_at": datetime.now().isoformat(),
        }

    # =========================================================
    # 원본 이미지를 Qwen 전송용 Data URL로 변환
    # =========================================================

    @staticmethod
    def image_to_data_url(file_path: str) -> str:
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(
                f"이미지 파일을 찾을 수 없습니다: {path}"
            )

        try:
            raw = path.read_bytes()
        except PermissionError as exc:
            raise PermissionError(
                f"이미지 파일 접근 권한이 없습니다: {path}"
            ) from exc

        if not raw:
            raise ValueError("내용이 없는 이미지 파일입니다.")

        mime_type, _ = mimetypes.guess_type(path)
        mime_type = mime_type or "image/png"

        encoded = base64.b64encode(raw).decode("utf-8")

        return f"data:{mime_type};base64,{encoded}"

    # =========================================================
    # 작은 이미지 OCR 정확도 개선용 확대
    # =========================================================

    def prepare_ocr_data_url(self, file_path: str) -> str:
        """작은 이미지만 로컬에서 확대해 OCR 입력 해상도를 확보한다.

        최종 이미지 분석에는 원본을 사용하고,
        OCR 1차 요청에만 필요 시 확대본을 사용한다.
        """

        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(
                f"이미지 파일을 찾을 수 없습니다: {path}"
            )

        raw = path.read_bytes()

        if not raw:
            raise ValueError("내용이 없는 이미지 파일입니다.")

        with Image.open(io.BytesIO(raw)) as image:
            if getattr(image, "is_animated", False):
                image.seek(0)

            max_edge = max(image.size)

            factor = max(
                1,
                self.client.config.image_ocr_upscale_factor,
            )

            small_max_edge = (
                self.client.config.image_ocr_small_max_edge
            )

            # 충분히 큰 이미지거나 확대 비율이 1이면 원본 사용
            if max_edge >= small_max_edge or factor == 1:
                return self.image_to_data_url(file_path)

            resized = image.resize(
                (
                    image.width * factor,
                    image.height * factor,
                ),
                Image.Resampling.LANCZOS,
            )

            output = io.BytesIO()
            resized.save(output, format="PNG")

        encoded = base64.b64encode(
            output.getvalue()
        ).decode("utf-8")

        return f"data:image/png;base64,{encoded}"

    # =========================================================
    # 1차 OCR
    # =========================================================

    def extract_ocr(
        self,
        file_path: str,
        image_url: Optional[str] = None,
    ) -> str:
        """Qwen3-VL을 OCR 전용으로 1회 호출한다."""

        image_url = (
            image_url
            or self.prepare_ocr_data_url(file_path)
        )

        return self.client.request_content(
            [
                self.client.image_part(image_url),
                self.client.text_part(OCR_PROMPT),
            ],
            max_tokens=100,
            timeout=self.client.config.timeout,
            temperature=0,
        ).strip()

    # =========================================================
    # 로컬 색상 분석
    # =========================================================

    @staticmethod
    def extract_colors(
        file_path: str,
        count: int = 5,
    ) -> list[dict]:
        """Qwen 추측이 아니라 실제 이미지 픽셀을 기반으로 대표 색상을 계산한다."""

        try:
            return extract_dominant_colors(
                file_path,
                count=count,
            )
        except Exception:
            # 색상 분석 실패가 전체 이미지 분석 실패로 이어지지 않도록 한다.
            return []

    @staticmethod
    def _build_color_context(
        dominant_colors: list[dict],
    ) -> str:
        if not dominant_colors:
            return "대표 색상 분석 결과 없음"

        lines = []

        for index, color in enumerate(
            dominant_colors,
            start=1,
        ):
            rgb = color.get("rgb", [])
            hex_value = color.get("hex", "")
            ratio = color.get("ratio", 0)

            lines.append(
                f"{index}. "
                f"HEX {hex_value}, "
                f"RGB {rgb}, "
                f"점유율 약 {ratio}%"
            )

        return "\n".join(lines)

    # =========================================================
    # 이미지 자동 분석
    # =========================================================

    def analyze_image(
        self,
        file_path: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        file_info = self._file_info(file_path)

        try:
            # 최종 분석은 원본 이미지 사용
            image_url = self.image_to_data_url(file_path)

            # OCR은 작은 이미지라면 확대본 사용 가능
            ocr_text = self.extract_ocr(file_path)

            # 실제 픽셀 기반 대표 색상
            dominant_colors = self.extract_colors(
                file_path,
                count=5,
            )

            color_context = self._build_color_context(
                dominant_colors
            )

            context_text = (
                f"\n추가 context:\n{context}"
                if context
                else ""
            )

            prompt = f"""
당신은 스마트 폴더 파일 분석기입니다.

현재 분석하는 파일 정보:
- 파일명: {file_info['original_name']}
- 파일 형식: {file_info['file_extension']}
- 이미지에서 OCR로 읽은 텍스트: {ocr_text}

로컬 픽셀 분석으로 계산한 대표 색상:
{color_context}

중요:
대표 색상의 HEX, RGB, 점유율은
실제 이미지 픽셀을 로컬에서 계산한 결과입니다.

색상에 대해 설명할 경우
임의로 다른 HEX 값을 추측하지 말고
위 계산 결과를 기준으로 사용하세요.

{context_text}

원본 이미지와 OCR 결과를 함께 참고하여
파일의 내용을 분석하세요.

OCR 결과를 임의로 다른 단어로 변경하지 말고,
이미지에서 확인되는 내용만 작성하세요.

반드시 JSON만 반환하고
마크다운 코드블록은 사용하지 마세요.

{{
  "display_name": "확장자를 제외한 간결한 한글 파일 제목",
  "tags": ["검색과 분류에 유용한 태그 3~5개"],
  "description": "이미지 내용과 용도를 설명하는 1~2문장",
  "ocr_text": "OCR로 확인한 원문",
  "category": "대분류",
  "sub_category": "소분류",
  "suggested_folder": "일반적인 추천 폴더 경로",
  "document_type": "파일 종류",
  "confidence": 0.0
}}
""".strip()

            parsed = self.client.request_json(
                [
                    self.client.image_part(image_url),
                    self.client.text_part(prompt),
                ],
                max_tokens=min(
                    500,
                    self.client.config.max_tokens,
                ),
                timeout=self.client.config.timeout,
                temperature=0,
            )

            # OCR 결과는 모델이 다시 바꾸지 못하게 1차 OCR 결과로 덮어쓴다.
            parsed["ocr_text"] = ocr_text

            return self._success_response(
                file_info=file_info,
                parsed=parsed,
                dominant_colors=dominant_colors,
            )

        except (
            AIClientError,
            OSError,
            ValueError,
        ) as exc:
            return self.build_fallback_response(
                file_info,
                str(exc),
            )

    # =========================================================
    # 이미지 자연어 질문
    # =========================================================

    def ask_image(
        self,
        file_path: str,
        user_prompt: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:

        image_url = self.image_to_data_url(file_path)

        ocr_text = self.extract_ocr(file_path)

        dominant_colors = self.extract_colors(
            file_path,
            count=5,
        )

        color_context = self._build_color_context(
            dominant_colors
        )

        context_text = (
            f"\n추가 context:\n{context}"
            if context
            else ""
        )

        prompt = f"""
원본 이미지와 아래 정보를 함께 참고하여
사용자의 질문에 답하세요.

이미지에서 확인되지 않는 내용은 추측하지 마세요.

OCR 결과:
{ocr_text}

로컬 픽셀 분석으로 계산한 대표 색상:
{color_context}

중요:
HEX, RGB, 색상 점유율과 관련된 질문에서는
위 로컬 계산값을 우선 사용하세요.

이미지를 보고 임의의 대표 HEX 값을
새로 만들어내지 마세요.

{context_text}

사용자 질문:
{user_prompt}
""".strip()

        return self.client.request_content(
            [
                self.client.image_part(image_url),
                self.client.text_part(prompt),
            ],
            timeout=self.client.config.timeout,
            max_tokens=self.client.config.max_tokens,
            temperature=0,
        )

    # =========================================================
    # 성공 응답
    # =========================================================

    @staticmethod
    def _success_response(
        file_info: Dict[str, Any],
        parsed: Dict[str, Any],
        dominant_colors: Optional[list[dict]] = None,
    ) -> Dict[str, Any]:

        default_name = os.path.splitext(
            file_info["original_name"]
        )[0]

        tags = parsed.get("tags", [])

        if not isinstance(tags, list):
            tags = [str(tags)] if tags else []

        tags = [
            str(tag).lstrip("#")
            for tag in tags
            if str(tag).strip()
        ]

        description = str(
            parsed.get("description")
            or parsed.get("summary")
            or ""
        )

        ai_comment = (
            "태그: "
            + (
                ", ".join(
                    "#" + tag
                    for tag in tags
                )
                if tags
                else "#이미지"
            )
            + f" / 코멘트: {description}"
        )

        metadata = {
            "@TYPE": "@DB",
            "display_name": str(
                parsed.get("display_name")
                or default_name
            ),
            "tags": tags,
            "description": description,
            "ai_comment": ai_comment,
            "ocr_text": str(
                parsed.get("ocr_text")
                or ""
            ),
            "category": parsed.get("category", ""),
            "sub_category": parsed.get("sub_category", ""),
            "suggested_folder": parsed.get("suggested_folder", ""),
            "document_type": parsed.get("document_type", "이미지"),
            "confidence": parsed.get("confidence", 0.0),
            "dominant_colors": (dominant_colors or []),
        }

        return {
            "@TYPE": "@DB",
            "status": "SUCCESS",
            "file_info": file_info,
            "metadata": metadata,
            "error": None,
        }

    # =========================================================
    # 실패 응답
    # =========================================================

    @staticmethod
    def build_fallback_response(
        file_info: Dict[str, Any],
        error_message: str,
    ) -> Dict[str, Any]:

        name = file_info.get(
            "original_name",
            "unknown",
        )

        default_name = os.path.splitext(name)[0]

        return {
            "@TYPE": "@DB",
            "status": "FAILED",
            "file_info": file_info,
            "metadata": {
                "@TYPE": "@DB",
                "display_name": default_name,
                "tags": [],
                "description": (
                    f"분석 실패: {error_message}"
                ),
                "ai_comment": (
                    "#분석실패 / 코멘트: "
                    f"{error_message}"
                ),
                "ocr_text": "",
                "dominant_colors": [],
            },
            "error": error_message,
        }
