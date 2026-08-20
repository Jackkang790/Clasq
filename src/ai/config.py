"""Environment-backed settings for the Clasq AI inference layer."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_clamped_int(name: str, default: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, _env_int(name, default)))


@dataclass(frozen=True)
class AIConfig:
    base_url: str = field(default_factory=lambda: os.getenv("AI_BASE_URL", "http://127.0.0.1:8100/v1"))
    model: str = field(default_factory=lambda: os.getenv("AI_MODEL", "qwen3-vl-8b"))
    timeout: int = field(default_factory=lambda: _env_int("AI_TIMEOUT", 300))
    video_ai_timeout: int = field(default_factory=lambda: _env_int("VIDEO_AI_TIMEOUT", 900))
    max_tokens: int = field(default_factory=lambda: _env_int("AI_MAX_TOKENS", 1000))
    ai_concurrency: int = field(
        default_factory=lambda: _env_clamped_int("AI_CONCURRENCY", 2, 1, 4)
    )
    image_ocr_small_max_edge: int = field(default_factory=lambda: _env_int("IMAGE_OCR_SMALL_MAX_EDGE", 512))
    image_ocr_upscale_factor: int = field(default_factory=lambda: _env_int("IMAGE_OCR_UPSCALE_FACTOR", 4))

    video_scene_threshold: float = field(default_factory=lambda: _env_float("VIDEO_SCENE_THRESHOLD", 0.30))
    video_max_gap_seconds: int = field(default_factory=lambda: _env_int("VIDEO_MAX_GAP_SECONDS", 10))
    video_image_width: int = field(default_factory=lambda: _env_int("VIDEO_IMAGE_WIDTH", 640))
    video_max_frames: int = field(default_factory=lambda: _env_int("VIDEO_MAX_FRAMES", 24))
    ffmpeg_timeout: int = field(default_factory=lambda: _env_int("FFMPEG_TIMEOUT", 600))
    ffmpeg_path: Optional[str] = field(default_factory=lambda: os.getenv("FFMPEG_PATH") or None)

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"
