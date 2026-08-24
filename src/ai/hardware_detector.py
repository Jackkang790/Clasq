"""로컬 하드웨어 감지 모듈.

nvidia-smi (이미 설치됨) + ctypes.windll (Windows 기본) 만 사용.
외부 의존성 없음. 모든 오류는 내부에서 처리하며 HardwareInfo를 항상 반환한다.
"""
from __future__ import annotations

import ctypes
import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HardwareInfo:
    gpu_available: bool
    gpu_name: str
    gpu_vram_mb: int        # 전체 VRAM
    gpu_vram_free_mb: int   # 현재 여유 VRAM
    cuda_available: bool    # nvidia-smi 응답 성공 여부
    system_ram_mb: int


_NO_GPU = HardwareInfo(
    gpu_available=False,
    gpu_name="",
    gpu_vram_mb=0,
    gpu_vram_free_mb=0,
    cuda_available=False,
    system_ram_mb=0,
)


class HardwareDetector:
    """앱 시작 시 1회 호출. 결과를 캐시하여 반복 호출을 빠르게 처리한다."""

    def __init__(self):
        self._cached: HardwareInfo | None = None

    def detect(self) -> HardwareInfo:
        if self._cached is not None:
            return self._cached
        self._cached = self._run_detect()
        return self._cached

    # ── 내부 ────────────────────────────────────────────────────────────

    def _run_detect(self) -> HardwareInfo:
        gpu = self._query_nvidia_smi()
        ram_mb = self._query_system_ram()
        info = HardwareInfo(
            gpu_available=gpu["available"],
            gpu_name=gpu["name"],
            gpu_vram_mb=gpu["vram_total_mb"],
            gpu_vram_free_mb=gpu["vram_free_mb"],
            cuda_available=gpu["cuda_available"],
            system_ram_mb=ram_mb,
        )
        if info.gpu_available:
            log.info(
                "GPU: %s  VRAM: %d MB  여유: %d MB  RAM: %d MB",
                info.gpu_name, info.gpu_vram_mb, info.gpu_vram_free_mb, info.system_ram_mb,
            )
        else:
            log.info("NVIDIA GPU 없음  RAM: %d MB", info.system_ram_mb)
        return info

    @staticmethod
    def _query_nvidia_smi() -> dict:
        """nvidia-smi 로 첫 번째 GPU 정보를 읽는다."""
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                timeout=5,
            ).strip()
            # 멀티 GPU 환경에서는 첫 번째 GPU만 사용
            first_line = out.splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            name      = parts[0]
            vram_total = int(parts[1])
            vram_free  = int(parts[2])
            return {
                "available":    True,
                "name":         name,
                "vram_total_mb": vram_total,
                "vram_free_mb":  vram_free,
                "cuda_available": True,
            }
        except FileNotFoundError:
            log.debug("nvidia-smi not found")
        except subprocess.TimeoutExpired:
            log.warning("nvidia-smi timeout")
        except Exception as exc:
            log.debug("nvidia-smi query failed: %s", exc)
        return {
            "available": False,
            "name": "",
            "vram_total_mb": 0,
            "vram_free_mb": 0,
            "cuda_available": False,
        }

    @staticmethod
    def _query_system_ram() -> int:
        """Windows GlobalMemoryStatusEx 로 시스템 RAM(MB)을 반환한다."""
        try:
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength",                ctypes.c_ulong),
                    ("dwMemoryLoad",            ctypes.c_ulong),
                    ("ullTotalPhys",            ctypes.c_ulonglong),
                    ("ullAvailPhys",            ctypes.c_ulonglong),
                    ("ullTotalPageFile",        ctypes.c_ulonglong),
                    ("ullAvailPageFile",        ctypes.c_ulonglong),
                    ("ullTotalVirtual",         ctypes.c_ulonglong),
                    ("ullAvailVirtual",         ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys // (1024 * 1024))
        except Exception as exc:
            log.debug("RAM query failed: %s", exc)
            return 0
