"""Environment-backed settings for the Clasq AI inference layer."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _runtime_dir() -> str:
    """앱 번들 내 runtime 디렉터리 경로.

    app_paths 모듈을 사용해 frozen / 개발 환경 양쪽에서 올바른 경로 반환.
    """
    from src.utils.app_paths import runtime_dir
    return runtime_dir()


def _find_ffmpeg() -> Optional[str]:
    """ffmpeg 탐색 우선순위.

    1. FFMPEG_PATH 환경변수
    2. 앱 번들 runtime/ffmpeg.exe
    3. None (video_analyzer.find_ffmpeg 가 PATH, 하드코딩 경로 탐색)
    """
    env = os.getenv("FFMPEG_PATH")
    if env:
        return env
    bundled = os.path.join(_runtime_dir(), "ffmpeg.exe")
    if os.path.isfile(bundled):
        return bundled
    return None


def _find_llama_server() -> str:
    """llama-server.exe 탐색 우선순위.

    1. LLAMA_SERVER_EXE 환경변수
    2. 앱 번들 runtime/llama-server.exe
    3. 개발 환경 기본값 (C:\\llama-cpp\\bin)
    """
    env = os.getenv("LLAMA_SERVER_EXE")
    if env:
        return env
    bundled = os.path.join(_runtime_dir(), "llama-server.exe")
    if os.path.isfile(bundled):
        return bundled
    return r"C:\llama-cpp\bin\llama-server.exe"


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
    base_url: str = field(default_factory=lambda: os.getenv("AI_BASE_URL", "http://127.0.0.1:8080/v1"))
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
    # ffmpeg: 번들 runtime/ → FFMPEG_PATH 환경변수 → None (video_analyzer가 PATH 탐색)
    ffmpeg_path: Optional[str] = field(default_factory=_find_ffmpeg)

    # llama-server 실행 관리 설정
    # LLAMA_MANAGED=false 로 설정하면 server_manager가 서버를 시작/종료하지 않음 (외부 서버 사용)
    llama_managed: bool = field(
        default_factory=lambda: os.getenv("LLAMA_MANAGED", "true").strip().lower() not in ("false", "0", "no")
    )
    # llama-server.exe: 번들 runtime/ → LLAMA_SERVER_EXE 환경변수 → 개발 환경 기본값
    llama_server_exe: str = field(default_factory=_find_llama_server)
    # 모델 기본 위치: %LOCALAPPDATA%\Clasq\models (앱 재설치 시에도 유지)
    # 환경변수 LLAMA_MODEL_PATH / LLAMA_MMPROJ_PATH 로 덮어쓸 수 있음
    llama_model_path: str = field(
        default_factory=lambda: os.getenv(
            "LLAMA_MODEL_PATH",
            os.path.join(
                os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                "Clasq", "models", "qwen3vl-8b-q4_k_m.gguf",
            ),
        )
    )
    llama_mmproj_path: str = field(
        default_factory=lambda: os.getenv(
            "LLAMA_MMPROJ_PATH",
            os.path.join(
                os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                "Clasq", "models", "mmproj-bf16.gguf",
            ),
        )
    )
    llama_host: str = field(default_factory=lambda: os.getenv("LLAMA_HOST", "127.0.0.1"))
    llama_port: int = field(default_factory=lambda: _env_int("LLAMA_PORT", 8080))
    llama_n_gpu_layers: int = field(default_factory=lambda: _env_int("LLAMA_N_GPU_LAYERS", 99))
    llama_context_size: int = field(default_factory=lambda: _env_int("LLAMA_CONTEXT_SIZE", 32768))
    llama_startup_timeout: int = field(default_factory=lambda: _env_int("LLAMA_STARTUP_TIMEOUT", 120))

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"
