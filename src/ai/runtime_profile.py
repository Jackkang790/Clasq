"""AI 추론 실행 프로필 및 프로필 선택기.

실제 검증이 완료된 프로필만 등록한다.
다른 GPU에 대한 설정은 검증 전까지 추가하지 않는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .hardware_detector import HardwareInfo

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeProfile:
    # ── 식별 ────────────────────────────────────────────────────────────
    name: str
    description: str

    # ── 모델 파일 ────────────────────────────────────────────────────────
    model_filename: str
    model_url: str
    model_sha256: str       # 전체 소문자 hex
    model_size_bytes: int   # 다운로드 진행률 표시용

    mmproj_filename: str
    mmproj_url: str
    mmproj_sha256: str
    mmproj_size_bytes: int

    # ── llama-server 실행 옵션 (벤치마크 검증값) ─────────────────────────
    n_gpu_layers: int
    context_size: int
    extra_args: tuple = field(default_factory=tuple)

    # ── 프로필 선택 기준 ─────────────────────────────────────────────────
    min_vram_mb: int = 0
    min_ram_mb: int  = 0

    @property
    def total_download_bytes(self) -> int:
        return self.model_size_bytes + self.mmproj_size_bytes

    @property
    def total_download_gb(self) -> float:
        return round(self.total_download_bytes / 1024 ** 3, 2)


# ---------------------------------------------------------------------------
# 검증된 프로필 목록
# ---------------------------------------------------------------------------

# RTX 3090 (24GB) — 유일한 검증 프로필
# 측정값: VRAM 사용 13,851 MB, 속도 텍스트 0.43s / 이미지 0.63s / 24프레임 9.80s
PROFILE_QWEN3VL_8B_Q4KM_CUDA = RuntimeProfile(
    name="qwen3vl-8b-q4km-cuda",
    description="Qwen3-VL-8B Q4_K_M (검증: RTX 3090 24GB)",

    # unsloth 저장소 — 벤치마크에 사용한 원본 파일
    model_filename="qwen3vl-8b-q4_k_m.gguf",
    model_url=(
        "https://huggingface.co/unsloth/Qwen3-VL-8B-Instruct-GGUF"
        "/resolve/main/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
    ),
    model_sha256="108e7ff92b78eefd3db4741885104acba514255c11b617d3c7b197a5f46efe89",
    model_size_bytes=5_027_785_568,  # 실측

    mmproj_filename="mmproj-bf16.gguf",
    mmproj_url=(
        "https://huggingface.co/unsloth/Qwen3-VL-8B-Instruct-GGUF"
        "/resolve/main/mmproj-BF16.gguf"
    ),
    mmproj_sha256="6516bb64bae1503a0fcd7ec9fa39655f8c481580be0a0a066397941d9761c9f4",
    mmproj_size_bytes=1_162_569_280,  # 실측

    # 벤치마크 검증값 그대로
    n_gpu_layers=99,
    context_size=32768,
    extra_args=("--log-disable",),

    # 실측 VRAM 13,851 MB + 약 500 MB 여유
    min_vram_mb=14_336,
    min_ram_mb=8_192,
)

# 향후 추가 예정 (검증 완료 후):
#   PROFILE_QWEN3VL_8B_Q4KM_12GB  — 12GB GPU 프로필
#   PROFILE_QWEN3VL_8B_Q4KM_8GB   — 8GB GPU 프로필
#   PROFILE_CPU_FALLBACK           — CPU 전용 프로필

# 검증된 프로필 목록 (우선순위 순)
_ALL_PROFILES: tuple[RuntimeProfile, ...] = (
    PROFILE_QWEN3VL_8B_Q4KM_CUDA,
)


# ---------------------------------------------------------------------------
# 프로필 선택기
# ---------------------------------------------------------------------------

class ProfileSelector:
    """HardwareInfo 를 받아 실행 가능한 RuntimeProfile 을 반환한다.

    지원하지 않는 환경에서는 None 을 반환하고 reason 에 이유를 저장한다.
    검증되지 않은 GPU 에 임의의 프로필을 강제 적용하지 않는다.
    """

    def __init__(self):
        self._reason: str = ""

    @property
    def reason(self) -> str:
        """마지막 select() 호출에서 프로필을 선택하지 못한 이유."""
        return self._reason

    def select(self, hw: HardwareInfo) -> Optional[RuntimeProfile]:
        self._reason = ""

        if not hw.gpu_available:
            self._reason = "NVIDIA GPU를 찾을 수 없습니다. NVIDIA 드라이버를 설치하세요."
            log.warning("ProfileSelector: %s", self._reason)
            return None

        if not hw.cuda_available:
            self._reason = (
                "CUDA를 사용할 수 없습니다. "
                "NVIDIA 드라이버가 올바르게 설치되었는지 확인하세요."
            )
            log.warning("ProfileSelector: %s", self._reason)
            return None

        for profile in _ALL_PROFILES:
            if hw.gpu_vram_mb >= profile.min_vram_mb:
                log.info(
                    "ProfileSelector: '%s' 선택 (VRAM %d MB >= %d MB)",
                    profile.name, hw.gpu_vram_mb, profile.min_vram_mb,
                )
                return profile

        self._reason = (
            f"지원하는 GPU 프로필이 없습니다. "
            f"감지된 VRAM: {hw.gpu_vram_mb:,} MB "
            f"(필요 최소: {_ALL_PROFILES[-1].min_vram_mb:,} MB 이상)"
        )
        log.warning("ProfileSelector: %s", self._reason)
        return None
