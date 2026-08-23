"""Representative-frame video analysis based on test_video_frames.py."""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .qwen_client import AIClientError, QwenClient


class VideoProcessingError(RuntimeError):
    pass


class FFmpegNotFoundError(VideoProcessingError):
    pass


class FFmpegExecutionError(VideoProcessingError):
    pass


class VideoAnalyzer:
    def __init__(self, client: Optional[QwenClient] = None) -> None:
        self.client = client or QwenClient()
        self.config = self.client.config

    def find_ffmpeg(self) -> str:
        configured = self.config.ffmpeg_path
        if configured:
            configured_path = Path(configured)
            if configured_path.is_file():
                return str(configured_path)

        discovered = shutil.which("ffmpeg")
        if discovered:
            return discovered

        windows_fallback = Path(r"C:\ffmpeg\bin\ffmpeg.exe")
        if windows_fallback.is_file():
            return str(windows_fallback)
        raise FFmpegNotFoundError("FFmpeg를 찾을 수 없습니다. FFMPEG_PATH 또는 PATH를 확인하세요.")

    @staticmethod
    def format_time(seconds: float) -> str:
        rounded = max(0, int(round(seconds)))
        hours = rounded // 3600
        minutes = (rounded % 3600) // 60
        secs = rounded % 60
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    @staticmethod
    def reduce_frames(
        frames: Sequence[Path], timestamps: Sequence[float], max_frames: int
    ) -> Tuple[List[Path], List[float]]:
        if len(frames) <= max_frames:
            return list(frames), list(timestamps)
        if max_frames <= 1:
            return [frames[0]], [timestamps[0]]
        indices = sorted({
            round(i * (len(frames) - 1) / (max_frames - 1))
            for i in range(max_frames)
        })
        return [frames[i] for i in indices], [timestamps[i] for i in indices]

    @staticmethod
    def image_to_data_url(path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(path)
        mime_type = mime_type or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    def extract_representative_frames(
        self, video_path: str, output_dir: Path
    ) -> Tuple[List[Path], List[float]]:
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {path}")
        if not path.is_file() or path.stat().st_size == 0:
            raise VideoProcessingError("내용이 없는 영상 파일입니다.")

        ffmpeg = self.find_ffmpeg()
        output_pattern = str(output_dir / "frame_%04d.jpg")
        filter_expression = (
            "select='"
            "isnan(prev_selected_t)"
            f"+gt(scene,{self.config.video_scene_threshold})"
            f"+gte(t-prev_selected_t,{self.config.video_max_gap_seconds})"
            "',"
            f"scale={self.config.video_image_width}:-2,"
            "showinfo"
        )
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "info", "-i", str(path),
            "-an", "-vf", filter_expression, "-fps_mode", "vfr",
            "-q:v", "3", output_pattern,
        ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.ffmpeg_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FFmpegExecutionError(
                f"FFmpeg 실행 시간이 {self.config.ffmpeg_timeout}초를 초과했습니다."
            ) from exc
        except OSError as exc:
            raise FFmpegExecutionError(f"FFmpeg 실행 실패: {exc}") from exc

        if result.returncode != 0:
            detail = result.stderr[-2000:].strip()
            raise FFmpegExecutionError(f"FFmpeg 처리 실패: {detail}")

        frames = sorted(output_dir.glob("frame_*.jpg"))
        if not frames:
            raise FFmpegExecutionError("영상에서 대표 프레임을 추출하지 못했습니다.")

        timestamp_pattern = re.compile(r"pts_time:([\-0-9.]+)")
        timestamps: List[float] = []
        for line in result.stderr.splitlines():
            if "showinfo" not in line:
                continue
            match = timestamp_pattern.search(line)
            if match:
                try:
                    timestamps.append(float(match.group(1)))
                except ValueError:
                    pass

        if len(timestamps) != len(frames):
            timestamps = [
                i * self.config.video_max_gap_seconds for i in range(len(frames))
            ]
        return self.reduce_frames(frames, timestamps, self.config.video_max_frames)

    def analyze_video(
        self,
        video_path: str,
        user_prompt: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        file_info = self._file_info(video_path)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                frames, timestamps = self.extract_representative_frames(
                    video_path, Path(temp_dir)
                )
                parsed = self._request_analysis(frames, timestamps, user_prompt, context)
            return self._success_response(file_info, parsed)
        except (AIClientError, VideoProcessingError, OSError, ValueError) as exc:
            return self.build_fallback_response(file_info, str(exc))

    def ask_video(
        self,
        video_path: str,
        user_prompt: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        with tempfile.TemporaryDirectory() as temp_dir:
            frames, timestamps = self.extract_representative_frames(video_path, Path(temp_dir))
            content = self._build_content(frames, timestamps, user_prompt, context, json_output=False)
            return self.client.request_content(
                content,
                timeout=self.config.video_ai_timeout,
                max_tokens=self.config.max_tokens,
                temperature=0,
            )

    def _request_analysis(
        self,
        frames: Sequence[Path],
        timestamps: Sequence[float],
        user_prompt: Optional[str],
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        content = self._build_content(frames, timestamps, user_prompt, context, json_output=True)
        return self.client.request_json(
            content,
            timeout=self.config.video_ai_timeout,
            max_tokens=self.config.max_tokens,
            temperature=0,
        )

    def _build_content(
        self,
        frames: Sequence[Path],
        timestamps: Sequence[float],
        user_prompt: Optional[str],
        context: Optional[Dict[str, Any]],
        *,
        json_output: bool,
    ) -> List[Dict[str, Any]]:
        context_text = f"\n추가 context: {context}" if context else ""
        question_text = f"\n사용자 질문: {user_prompt}" if user_prompt else ""
        if json_output:
            output_instruction = """
반드시 JSON만 반환하고 마크다운 코드블록은 사용하지 마세요.
{
  "display_name": "확장자를 제외한 간결한 영상 제목",
  "tags": ["검색과 분류에 유용한 태그 3~5개"],
  "description": "영상 전체 내용을 2~4문장으로 요약",
  "video_type": "영상 종류",
  "summary": "영상 전체 요약",
  "timeline": [{"time": "00:00", "scene": "주요 작업"}],
  "applications": ["확실하게 확인된 프로그램"],
  "visible_text": ["영상 이해에 중요한 텍스트"],
  "category": "대분류",
  "sub_category": "소분류",
  "suggested_folder": "추천 폴더"
}
"""
        else:
            output_instruction = "사용자 질문에 자연어로 정확하고 간결하게 답하세요."

        prompt = f"""
이후 제공되는 이미지들은 하나의 동영상에서 화면 변화가 발생한 순간과
일정 시간 간격으로 추출한 대표 프레임이며 실제 시간 순서로 제공됩니다.

- 실제 이미지에서 확인되는 내용만 작성하세요.
- 화면에 없는 프로그램명, 파일명, URL이나 사용자 행동을 추측하지 마세요.
- 동일 작업을 timeline에 반복하지 마세요.
- 프로그램이나 작업이 실제로 변경되는 시점을 중심으로 분석하세요.
- applications에는 확실하게 확인된 프로그램만 작성하세요.
- visible_text에는 영상 이해에 도움이 되는 텍스트만 기록하세요.
{context_text}{question_text}

{output_instruction}
""".strip()
        content: List[Dict[str, Any]] = [self.client.text_part(prompt)]
        for frame, timestamp in zip(frames, timestamps):
            content.append(self.client.text_part(
                f"다음 이미지는 영상 시각 {self.format_time(timestamp)}입니다."
            ))
            content.append(self.client.image_part(self.image_to_data_url(frame)))
        return content

    @staticmethod
    def _file_info(file_path: str) -> Dict[str, Any]:
        path = Path(file_path)
        return {
            "original_name": path.name,
            "file_extension": path.suffix.lower(),
            "file_size_bytes": path.stat().st_size if path.exists() else 0,
            "analyzed_at": datetime.now().isoformat(),
        }

    @staticmethod
    def _success_response(file_info: Dict[str, Any], parsed: Dict[str, Any]) -> Dict[str, Any]:
        default_name = os.path.splitext(file_info["original_name"])[0]
        tags = parsed.get("tags", [])
        if not isinstance(tags, list):
            tags = [str(tags)] if tags else []
        tags = [str(tag).lstrip("#") for tag in tags if str(tag).strip()]
        description = str(parsed.get("description") or parsed.get("summary") or "")
        ai_comment = f"태그: {', '.join('#' + tag for tag in tags) if tags else '#영상'} / 코멘트: {description}"
        metadata = dict(parsed)
        metadata.update({
            "@TYPE": "@DB", "display_name": str(parsed.get("display_name") or default_name),
            "tags": tags, "description": description, "ai_comment": ai_comment,
            "ocr_text": "",
        })
        return {"@TYPE": "@DB", "status": "SUCCESS", "file_info": file_info, "metadata": metadata, "error": None}

    @staticmethod
    def build_fallback_response(file_info: Dict[str, Any], error_message: str) -> Dict[str, Any]:
        name = file_info.get("original_name", "unknown")
        default_name = os.path.splitext(name)[0]
        return {
            "@TYPE": "@DB", "status": "FAILED", "file_info": file_info,
            "metadata": {
                "@TYPE": "@DB", "display_name": default_name, "tags": [],
                "description": f"분석 실패: {error_message}",
                "ai_comment": f"#분석실패 / 코멘트: {error_message}", "ocr_text": "",
            },
            "error": error_message,
        }
