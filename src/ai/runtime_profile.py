"""AI 추론 실행 프로필 및 프로필 선택기.

프로필 등록 정책:
- 16GB 이상 (qwen3vl-8b-q4km-cuda): RTX 3090 실측 검증 완료.
- 12GB 클래스 (qwen3vl-8b-q4km-12gb): 단일 실측 + KV 비례 추정, 실기기 미검증.
- 8GB 클래스 (qwen3vl-8b-q4km-8gb): 가능성 불확실, 베이스 모델이 8GB 초과 시 OOM 후 AI 비활성화.
모든 프로필은 동일한 모델 파일을 사용한다 (다운로드 1회).
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

# 12GB GPU 프로필 (미검증)
# 추정 근거:
#   RTX 3090에서 ctx=32768 → 13,851 MB (실측).
#   KV cache는 ctx에 비례하므로 ctx=8192(1/4)로 줄이면 KV ~1/4 절감.
#   KV 비중 추정(33-65%): ctx=8192 시 약 9,200-11,000 MB 예상.
#   min_vram_mb=10,240: 12GB GPU(12,288 MB)에서 ~2 GB OS/display 여유.
#   실기기(12GB GPU) 검증 미완료.
PROFILE_QWEN3VL_8B_Q4KM_12GB = RuntimeProfile(
    name="qwen3vl-8b-q4km-12gb",
    description="Qwen3-VL-8B Q4_K_M — 12GB GPU 클래스 (추정, ctx=8192, 실기기 미검증)",

    model_filename="qwen3vl-8b-q4_k_m.gguf",
    model_url=(
        "https://huggingface.co/unsloth/Qwen3-VL-8B-Instruct-GGUF"
        "/resolve/main/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
    ),
    model_sha256="108e7ff92b78eefd3db4741885104acba514255c11b617d3c7b197a5f46efe89",
    model_size_bytes=5_027_785_568,

    mmproj_filename="mmproj-bf16.gguf",
    mmproj_url=(
        "https://huggingface.co/unsloth/Qwen3-VL-8B-Instruct-GGUF"
        "/resolve/main/mmproj-BF16.gguf"
    ),
    mmproj_sha256="6516bb64bae1503a0fcd7ec9fa39655f8c481580be0a0a066397941d9761c9f4",
    mmproj_size_bytes=1_162_569_280,

    n_gpu_layers=99,
    context_size=8_192,
    extra_args=("--log-disable",),

    min_vram_mb=10_240,
    min_ram_mb=8_192,
)

# 8GB GPU 프로필 (미검증, 가능성 불확실)
# 추정 근거:
#   단일 측정값(13,851 MB, ctx=32768)에서 KV vs 베이스 모델 비중 불분명.
#   베이스 모델 VRAM 추정 범위: 7.3-9.2 GB (KV 비중 가정에 따라 다름).
#   베이스가 8GB 초과 시 OOM → cuda_oom 판정 → AI 비활성화 (안전 동작).
#   ctx=2048: 최소 기본 이미지·단일 문서 분석 가능 범위.
#   실기기(8GB GPU) 미검증, OOM 발생 가능성 높음.
PROFILE_QWEN3VL_8B_Q4KM_8GB = RuntimeProfile(
    name="qwen3vl-8b-q4km-8gb",
    description="Qwen3-VL-8B Q4_K_M — 8GB GPU 클래스 (미검증, OOM 가능, ctx=2048)",

    model_filename="qwen3vl-8b-q4_k_m.gguf",
    model_url=(
        "https://huggingface.co/unsloth/Qwen3-VL-8B-Instruct-GGUF"
        "/resolve/main/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
    ),
    model_sha256="108e7ff92b78eefd3db4741885104acba514255c11b617d3c7b197a5f46efe89",
    model_size_bytes=5_027_785_568,

    mmproj_filename="mmproj-bf16.gguf",
    mmproj_url=(
        "https://huggingface.co/unsloth/Qwen3-VL-8B-Instruct-GGUF"
        "/resolve/main/mmproj-BF16.gguf"
    ),
    mmproj_sha256="6516bb64bae1503a0fcd7ec9fa39655f8c481580be0a0a066397941d9761c9f4",
    mmproj_size_bytes=1_162_569_280,

    n_gpu_layers=99,
    context_size=2_048,
    extra_args=("--log-disable",),

    # 8GB GPU(8,192 MB 전체)에서 ~1 GB OS/display 여유 가정
    # 실제 OOM 시 failure_kind="cuda_oom" → 상위 호출자가 AI 비활성화
    min_vram_mb=7_168,
    min_ram_mb=8_192,
)

# 등록된 프로필 목록 (ProfileSelector가 min_vram_mb 내림차순으로 정렬하여 사용)
_ALL_PROFILES: tuple[RuntimeProfile, ...] = (
    PROFILE_QWEN3VL_8B_Q4KM_CUDA,   # 16GB+ 클래스 (RTX 3090 검증)
    PROFILE_QWEN3VL_8B_Q4KM_12GB,   # 12GB 클래스 (추정)
    PROFILE_QWEN3VL_8B_Q4KM_8GB,    # 8GB 클래스 (미검증)
)


# ---------------------------------------------------------------------------
# 프로필 선택기
# ---------------------------------------------------------------------------

class ProfileSelector:
    """HardwareInfo 를 받아 실행 가능한 RuntimeProfile 을 반환한다.

    지원하지 않는 환경에서는 None 을 반환하고 reason 에 이유를 저장한다.
    검증되지 않은 GPU 에 임의의 프로필을 강제 적용하지 않는다.
    """

    def __init__(self, profiles=None):
        self._reason: str = ""
        self._profiles = tuple(profiles) if profiles is not None else _ALL_PROFILES

    @property
    def reason(self) -> str:
        """마지막 select() 호출에서 프로필을 선택하지 못한 이유."""
        return self._reason

    def select(self, hw: HardwareInfo) -> Optional[RuntimeProfile]:
        candidates = self.select_candidates(hw)
        return candidates[0] if candidates else None

    def select_candidates(self, hw: HardwareInfo) -> tuple[RuntimeProfile, ...]:
        """Return eligible profiles in high-to-low fallback order.

        A profile must fit both the physical VRAM and the VRAM currently free.
        Keeping this ordering in one place prevents fallback from revisiting a
        profile or jumping back to a larger profile.
        """
        self._reason = ""

        if not hw.gpu_available:
            self._reason = "NVIDIA GPU를 찾을 수 없습니다. NVIDIA 드라이버를 설치하세요."
            log.warning("ProfileSelector: %s", self._reason)
            return ()

        if not hw.cuda_available:
            self._reason = (
                "CUDA를 사용할 수 없습니다. "
                "NVIDIA 드라이버가 올바르게 설치되었는지 확인하세요."
            )
            log.warning("ProfileSelector: %s", self._reason)
            return ()

        ordered = tuple(sorted(self._profiles, key=lambda p: p.min_vram_mb, reverse=True))
        candidates = tuple(
            profile for profile in ordered
            if hw.gpu_vram_mb >= profile.min_vram_mb
            and hw.gpu_vram_free_mb >= profile.min_vram_mb
            and (not profile.min_ram_mb or hw.system_ram_mb >= profile.min_ram_mb)
        )
        if candidates:
            log.info(
                "ProfileSelector: candidates=%s (total=%d MB, free=%d MB)",
                [profile.name for profile in candidates],
                hw.gpu_vram_mb,
                hw.gpu_vram_free_mb,
            )
            return candidates

        minimum = min((p.min_vram_mb for p in ordered), default=0)
        self._reason = (
            f"지원하는 GPU 프로필이 없습니다. "
            f"감지된 VRAM: 전체 {hw.gpu_vram_mb:,} MB, "
            f"여유 {hw.gpu_vram_free_mb:,} MB "
            f"(필요 최소: {minimum:,} MB 이상)"
        )
        log.warning("ProfileSelector: %s", self._reason)
        return ()
