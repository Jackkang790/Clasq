# -*- coding: utf-8 -*-
"""
VRAM 사용량 측정 벤치마크

측정 대상: Docker 컨테이너 안의 vLLM 서버 (Qwen3-VL-8B)
측정 방법: pynvml (호스트에서 Docker GPU 사용량 동일하게 읽힘) + nvidia-smi

실행 위치: 프로젝트 루트
  python benchmarks/benchmark_vram_usage.py
  python benchmarks/benchmark_vram_usage.py --video path/to/video.mp4
  python benchmarks/benchmark_vram_usage.py --image path/to/image.jpg
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.config import AIConfig


# ---------------------------------------------------------------------------
# GPU 측정
# ---------------------------------------------------------------------------

def _try_import_pynvml():
    try:
        import pynvml
        return pynvml
    except ImportError:
        return None


@dataclass
class GpuSnapshot:
    label: str
    timestamp: float
    total_mb: float
    used_mb: float
    free_mb: float
    process_mb: float        # 현재 프로세스(vLLM 포함) 사용량 합계
    torch_allocated_mb: float = 0.0
    torch_reserved_mb: float = 0.0
    source: str = "pynvml"

    @property
    def utilization_pct(self) -> float:
        return round(self.used_mb / self.total_mb * 100, 1) if self.total_mb else 0.0

    def __str__(self) -> str:
        return (
            f"[{self.label}] "
            f"전체={self.total_mb:.0f}MB  "
            f"사용={self.used_mb:.0f}MB ({self.utilization_pct}%)  "
            f"여유={self.free_mb:.0f}MB  "
            f"프로세스합계={self.process_mb:.0f}MB"
        )


class GpuMonitor:
    def __init__(self, device_index: int = 0):
        self.device_index = device_index
        self._pynvml = _try_import_pynvml()
        self._handle = None
        self._nvidia_smi_only = False

        if self._pynvml:
            try:
                self._pynvml.nvmlInit()
                self._handle = self._pynvml.nvmlDeviceGetHandleByIndex(device_index)
                info = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
                print(f"[GPU] pynvml 초기화 성공 -전체 VRAM: {info.total / 1024**2:.0f} MB")
            except Exception as exc:
                print(f"[GPU] pynvml 초기화 실패: {exc} -nvidia-smi fallback 사용")
                self._pynvml = None
                self._nvidia_smi_only = True
        else:
            print("[GPU] pynvml 미설치 -nvidia-smi fallback 사용 (pip install pynvml 권장)")
            self._nvidia_smi_only = True

    def _query_nvidia_smi(self) -> Tuple[float, float, float]:
        """(total_mb, used_mb, free_mb) -nvidia-smi로 읽기."""
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=memory.total,memory.used,memory.free",
                 "--format=csv,noheader,nounits",
                 f"--id={self.device_index}"],
                text=True, timeout=5,
            ).strip()
            parts = [float(x.strip()) for x in out.split(",")]
            return parts[0], parts[1], parts[2]
        except Exception:
            return 0.0, 0.0, 0.0

    def _process_vram_mb(self) -> float:
        """nvidia-smi로 GPU에 올라간 프로세스들의 VRAM 합계."""
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-compute-apps=pid,used_gpu_memory",
                 "--format=csv,noheader,nounits",
                 f"--id={self.device_index}"],
                text=True, timeout=5,
            ).strip()
            if not out:
                return 0.0
            total = 0.0
            for line in out.splitlines():
                parts = line.split(",")
                if len(parts) >= 2:
                    try:
                        total += float(parts[1].strip())
                    except ValueError:
                        pass
            return total
        except Exception:
            return 0.0

    def snapshot(self, label: str) -> GpuSnapshot:
        ts = time.time()
        process_mb = self._process_vram_mb()

        if self._pynvml and self._handle:
            try:
                info = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
                total_mb = info.total / 1024**2
                used_mb = info.used / 1024**2
                free_mb = info.free / 1024**2
                snap = GpuSnapshot(
                    label=label, timestamp=ts,
                    total_mb=total_mb, used_mb=used_mb, free_mb=free_mb,
                    process_mb=process_mb, source="pynvml",
                )
                print(snap)
                return snap
            except Exception:
                pass

        # fallback
        total_mb, used_mb, free_mb = self._query_nvidia_smi()
        snap = GpuSnapshot(
            label=label, timestamp=ts,
            total_mb=total_mb, used_mb=used_mb, free_mb=free_mb,
            process_mb=process_mb, source="nvidia-smi",
        )
        print(snap)
        return snap

    def close(self):
        if self._pynvml:
            try:
                self._pynvml.nvmlShutdown()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 합성 테스트 파일 생성
# ---------------------------------------------------------------------------

def make_test_image_path(tmp_dir: str, width: int = 640, height: int = 480) -> str:
    """PIL이 있으면 실제 이미지, 없으면 최소 JPEG 바이트."""
    path = os.path.join(tmp_dir, "test_image.jpg")
    try:
        from PIL import Image as PILImage, ImageDraw
        img = PILImage.new("RGB", (width, height), color=(100, 149, 237))
        draw = ImageDraw.Draw(img)
        draw.text((20, 20), "VRAM 측정용 테스트 이미지 / Clasq benchmark", fill=(255, 255, 255))
        draw.rectangle([50, 60, width - 50, height - 60], outline=(255, 200, 0), width=3)
        img.save(path, "JPEG", quality=85)
        return path
    except Exception:
        # 최소 JPEG (1x1 흰색)
        minimal_jpeg = bytes([
            0xFF,0xD8,0xFF,0xE0,0x00,0x10,0x4A,0x46,0x49,0x46,0x00,0x01,
            0x01,0x00,0x00,0x01,0x00,0x01,0x00,0x00,0xFF,0xDB,0x00,0x43,
            0x00,0x08,0x06,0x06,0x07,0x06,0x05,0x08,0x07,0x07,0x07,0x09,
            0x09,0x08,0x0A,0x0C,0x14,0x0D,0x0C,0x0B,0x0B,0x0C,0x19,0x12,
            0x13,0x0F,0x14,0x1D,0x1A,0x1F,0x1E,0x1D,0x1A,0x1C,0x1C,0x20,
            0x24,0x2E,0x27,0x20,0x22,0x2C,0x23,0x1C,0x1C,0x28,0x37,0x29,
            0x2C,0x30,0x31,0x34,0x34,0x34,0x1F,0x27,0x39,0x3D,0x38,0x32,
            0x3C,0x2E,0x33,0x34,0x32,0xFF,0xC0,0x00,0x0B,0x08,0x00,0x01,
            0x00,0x01,0x01,0x01,0x11,0x00,0xFF,0xC4,0x00,0x1F,0x00,0x00,
            0x01,0x05,0x01,0x01,0x01,0x01,0x01,0x01,0x00,0x00,0x00,0x00,
            0x00,0x00,0x00,0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,
            0x09,0x0A,0x0B,0xFF,0xC4,0x00,0xB5,0x10,0x00,0x02,0x01,0x03,
            0x03,0x02,0x04,0x03,0x05,0x05,0x04,0x04,0x00,0x00,0x01,0x7D,
            0x01,0x02,0x03,0x00,0x04,0x11,0x05,0x12,0x21,0x31,0x41,0x06,
            0x13,0x51,0x61,0x07,0x22,0x71,0x14,0x32,0x81,0x91,0xA1,0x08,
            0x23,0x42,0xB1,0xC1,0x15,0x52,0xD1,0xF0,0x24,0x33,0x62,0x72,
            0x82,0x09,0x0A,0x16,0x17,0x18,0x19,0x1A,0x25,0x26,0x27,0x28,
            0x29,0x2A,0x34,0x35,0x36,0x37,0x38,0x39,0x3A,0x43,0x44,0x45,
            0x46,0x47,0x48,0x49,0x4A,0x53,0x54,0x55,0x56,0x57,0x58,0x59,
            0x5A,0x63,0x64,0x65,0x66,0x67,0x68,0x69,0x6A,0x73,0x74,0x75,
            0x76,0x77,0x78,0x79,0x7A,0x83,0x84,0x85,0x86,0x87,0x88,0x89,
            0x8A,0x93,0x94,0x95,0x96,0x97,0x98,0x99,0x9A,0xA2,0xA3,0xA4,
            0xA5,0xA6,0xA7,0xA8,0xA9,0xAA,0xB2,0xB3,0xB4,0xB5,0xB6,0xB7,
            0xB8,0xB9,0xBA,0xC2,0xC3,0xC4,0xC5,0xC6,0xC7,0xC8,0xC9,0xCA,
            0xD2,0xD3,0xD4,0xD5,0xD6,0xD7,0xD8,0xD9,0xDA,0xE1,0xE2,0xE3,
            0xE4,0xE5,0xE6,0xE7,0xE8,0xE9,0xEA,0xF1,0xF2,0xF3,0xF4,0xF5,
            0xF6,0xF7,0xF8,0xF9,0xFA,0xFF,0xDA,0x00,0x08,0x01,0x01,0x00,
            0x00,0x3F,0x00,0xFB,0xD3,0xFF,0xD9,
        ])
        with open(path, "wb") as f:
            f.write(minimal_jpeg)
        return path


def image_to_data_url(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    import mimetypes
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


# ---------------------------------------------------------------------------
# vLLM 요청
# ---------------------------------------------------------------------------

def check_server(base_url: str, timeout: int = 10) -> bool:
    try:
        r = requests.get(f"{base_url}/models", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def infer_text(base_url: str, model: str, prompt: str, max_tokens: int = 200, timeout: int = 120) -> Tuple[str, float]:
    started = time.perf_counter()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    r = requests.post(f"{base_url}/chat/completions", json=payload, timeout=timeout)
    r.raise_for_status()
    elapsed = time.perf_counter() - started
    content = r.json()["choices"][0]["message"]["content"]
    return content, elapsed


def infer_image(base_url: str, model: str, image_data_url: str, prompt: str,
                max_tokens: int = 300, timeout: int = 180) -> Tuple[str, float]:
    started = time.perf_counter()
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data_url}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    r = requests.post(f"{base_url}/chat/completions", json=payload, timeout=timeout)
    r.raise_for_status()
    elapsed = time.perf_counter() - started
    content = r.json()["choices"][0]["message"]["content"]
    return content, elapsed


def infer_multi_image(base_url: str, model: str, image_data_urls: List[str], prompt: str,
                      max_tokens: int = 500, timeout: int = 300) -> Tuple[str, float]:
    """영상 분석 시뮬레이션 -여러 프레임을 한 요청으로."""
    started = time.perf_counter()
    content_parts = [{"type": "text", "text": prompt}]
    for idx, url in enumerate(image_data_urls):
        content_parts.append({"type": "text", "text": f"프레임 {idx + 1}:"})
        content_parts.append({"type": "image_url", "image_url": {"url": url}})
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content_parts}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    r = requests.post(f"{base_url}/chat/completions", json=payload, timeout=timeout)
    r.raise_for_status()
    elapsed = time.perf_counter() - started
    content = r.json()["choices"][0]["message"]["content"]
    return content, elapsed


# ---------------------------------------------------------------------------
# 메인 벤치마크
# ---------------------------------------------------------------------------

def run_benchmark(
    base_url: str,
    model: str,
    image_path: Optional[str] = None,
    video_path: Optional[str] = None,
    repeat: int = 3,
    device_index: int = 0,
) -> Dict:
    monitor = GpuMonitor(device_index=device_index)
    snapshots: List[GpuSnapshot] = []
    results: Dict = {}

    def snap(label: str) -> GpuSnapshot:
        s = monitor.snapshot(label)
        snapshots.append(s)
        return s

    print("\n" + "=" * 70)
    print("Clasq VRAM 측정 벤치마크")
    print(f"  서버: {base_url}  모델: {model}")
    print("=" * 70)

    # ── 서버 확인 ──────────────────────────────────────────────────────
    print("\n[1] 서버 연결 확인...")
    if not check_server(base_url):
        print(f"  ✗ vLLM 서버에 연결할 수 없습니다: {base_url}")
        print("    Docker 컨테이너가 실행 중인지 확인하세요.")
        monitor.close()
        return {}
    print("  ✓ 서버 연결 성공")

    # ── 베이스라인 (vLLM idle = 모델 로딩 완료 상태) ──────────────────
    print("\n[2] 베이스라인 측정 (vLLM idle -모델 로딩 완료)")
    time.sleep(2)
    snap_idle = snap("vLLM_idle")

    with tempfile.TemporaryDirectory() as tmp:

        # ── 텍스트 추론 ─────────────────────────────────────────────────
        print("\n[3] 텍스트 추론 측정...")
        text_snaps = []
        for i in range(repeat):
            try:
                content, elapsed = infer_text(
                    base_url, model,
                    "파일 정리 시스템에 대해 한 문장으로 설명해주세요.",
                    max_tokens=100,
                )
                time.sleep(0.5)
                s = snap(f"text_infer_{i+1}")
                text_snaps.append(s)
                print(f"  응답 시간: {elapsed:.2f}s  내용: {content[:60]}...")
            except Exception as exc:
                print(f"  텍스트 추론 실패: {exc}")

        # ── 이미지 추론 ─────────────────────────────────────────────────
        print("\n[4] 이미지 추론 측정...")
        test_image = image_path or make_test_image_path(tmp)
        image_url = image_to_data_url(test_image)
        print(f"  이미지: {test_image} ({os.path.getsize(test_image):,} bytes)")

        image_snaps = []
        for i in range(repeat):
            try:
                content, elapsed = infer_image(
                    base_url, model, image_url,
                    "이 이미지의 내용을 한국어로 간략히 설명해주세요.",
                    max_tokens=200,
                )
                time.sleep(0.5)
                s = snap(f"image_infer_{i+1}")
                image_snaps.append(s)
                print(f"  응답 시간: {elapsed:.2f}s  내용: {content[:60]}...")
            except Exception as exc:
                print(f"  이미지 추론 실패: {exc}")

        # ── OCR 추론 (이미지 분석기 실제 흐름 -이미지 2회 호출) ────────
        print("\n[5] OCR 추론 측정 (이미지 2회 호출 -실제 analyze_image 흐름)...")
        ocr_snaps = []
        for i in range(2):
            try:
                # 1차: OCR
                infer_image(base_url, model, image_url, "이미지에서 모든 텍스트를 추출하세요.", max_tokens=100)
                # 2차: 전체 분석
                infer_image(base_url, model, image_url,
                            "파일 내용을 JSON으로 분석해주세요: {\"display_name\":\"\",\"tags\":[],\"description\":\"\"}",
                            max_tokens=300)
                time.sleep(0.5)
                s = snap(f"ocr_full_{i+1}")
                ocr_snaps.append(s)
            except Exception as exc:
                print(f"  OCR 추론 실패: {exc}")

        # ── 동영상 프레임 시뮬레이션 ────────────────────────────────────
        print("\n[6] 영상 프레임 연속 처리 측정 (최대 24프레임 시뮬레이션)...")
        video_snaps = []
        frame_counts = [4, 8, 16, 24]

        for frame_count in frame_counts:
            frames = [image_url] * frame_count  # 동일 이미지로 프레임 수 시뮬레이션
            try:
                content, elapsed = infer_multi_image(
                    base_url, model, frames,
                    "이 영상 프레임들을 분석하여 내용을 요약해주세요.",
                    max_tokens=400,
                )
                time.sleep(1.0)
                s = snap(f"video_{frame_count}frames")
                video_snaps.append(s)
                print(f"  프레임 {frame_count}개: {elapsed:.2f}s  "
                      f"VRAM {s.used_mb:.0f}MB")
            except Exception as exc:
                print(f"  영상 프레임 {frame_count}개 실패: {exc}")
                break

        # 실제 비디오 파일이 있으면 처리
        if video_path and Path(video_path).exists():
            print(f"\n[6b] 실제 영상 파일 처리: {video_path}")
            try:
                import shutil
                if shutil.which("ffmpeg"):
                    from src.ai.video_analyzer import VideoAnalyzer
                    analyzer = VideoAnalyzer()
                    snap("before_real_video")
                    result = analyzer.analyze_video(video_path)
                    s = snap("after_real_video")
                    video_snaps.append(s)
                    print(f"  결과: {result.get('status')}  VRAM: {s.used_mb:.0f}MB")
                else:
                    print("  ffmpeg 없음 -실제 영상 처리 건너뜀")
            except Exception as exc:
                print(f"  실제 영상 처리 실패: {exc}")

        # ── 연속 처리 (누수 확인) ────────────────────────────────────────
        print("\n[7] 연속 처리 누수 확인 (이미지 10회 반복)...")
        leak_snaps = []
        for i in range(10):
            try:
                infer_image(base_url, model, image_url,
                            "이미지를 분석해주세요.", max_tokens=100)
            except Exception:
                pass
        time.sleep(2)
        s = snap("after_10_repeats")
        leak_snaps.append(s)

        for i in range(20):
            try:
                infer_image(base_url, model, image_url,
                            "이미지를 분석해주세요.", max_tokens=100)
            except Exception:
                pass
        time.sleep(2)
        s = snap("after_30_repeats")
        leak_snaps.append(s)

        # ── 종료 후 반환 확인 ────────────────────────────────────────────
        print("\n[8] 작업 종료 후 VRAM 반환 확인...")
        time.sleep(5)
        snap_final = snap("after_all_done")

    monitor.close()

    # ── 결과 집계 ────────────────────────────────────────────────────────
    all_used = [s.used_mb for s in snapshots if s.used_mb > 0]
    peak_mb = max(all_used) if all_used else 0
    idle_mb = snap_idle.used_mb
    total_mb = snap_idle.total_mb

    text_avg = (sum(s.used_mb for s in text_snaps) / len(text_snaps)) if text_snaps else 0
    image_avg = (sum(s.used_mb for s in image_snaps) / len(image_snaps)) if image_snaps else 0
    video_avg = (sum(s.used_mb for s in video_snaps) / len(video_snaps)) if video_snaps else 0
    final_mb = snap_final.used_mb

    leak_delta = 0.0
    if len(leak_snaps) >= 2:
        leak_delta = leak_snaps[-1].used_mb - leak_snaps[0].used_mb

    # 메모리 누수 판단: 30회 반복 후 증가량이 200MB 이상이면 누수 의심
    leak_detected = leak_delta > 200

    print("\n" + "=" * 70)
    print("측정 결과 요약")
    print("=" * 70)

    # GPU 모델
    gpu_name = "알 수 없음"
    try:
        gpu_name = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True, timeout=5,
        ).strip()
    except Exception:
        pass

    print(f"\nGPU 모델          : {gpu_name}")
    print(f"GPU 전체 VRAM     : {total_mb:.0f} MB ({total_mb/1024:.1f} GB)")
    print(f"모델 로딩 후 VRAM : {idle_mb:.0f} MB  ({idle_mb/1024:.1f} GB) -vLLM idle 기준")
    print(f"텍스트 추론 VRAM  : {text_avg:.0f} MB  ({text_avg/1024:.1f} GB) -평균")
    print(f"이미지 추론 VRAM  : {image_avg:.0f} MB  ({image_avg/1024:.1f} GB) -평균")
    print(f"영상 처리 VRAM    : {video_avg:.0f} MB  ({video_avg/1024:.1f} GB) -평균 (다중 프레임)")
    print(f"최대(Peak) VRAM   : {peak_mb:.0f} MB  ({peak_mb/1024:.1f} GB)")
    print(f"장시간 처리 후    : {final_mb:.0f} MB  ({final_mb/1024:.1f} GB)")
    print(f"메모리 누수 여부  : {'의심됨 (+{:.0f}MB)'.format(leak_delta) if leak_detected else '정상 ({}:{:.0f}MB 변동)'.format('±', abs(leak_delta))}")
    print(f"권장 최소 VRAM    : {peak_mb * 1.15 / 1024:.1f} GB  (peak × 1.15 여유)")

    print("\n── VRAM 환경별 실행 가능 여부 ─────────────────────────────────")
    def feasibility(vram_gb: float) -> str:
        vram_mb = vram_gb * 1024
        if vram_mb >= peak_mb * 1.15:
            return "✓ 실행 가능"
        elif vram_mb >= peak_mb * 0.9:
            return "△ 조건부 가능 (여유 부족, 배치/프레임 수 줄이기 권장)"
        elif vram_mb >= idle_mb * 1.05:
            return "▲ 제한적 가능 (텍스트 전용 또는 양자화 필요)"
        else:
            return "✗ 실행 어려움 (양자화 + 해상도 축소 + CPU offload 필요)"

    for gb in [6, 8, 12, 16]:
        print(f"  VRAM {gb:2d}GB : {feasibility(float(gb))}")

    print("\n── 최적화 검토 (VRAM 부족 시) ─────────────────────────────────")
    optimizations = [
        ("FP16 / BF16",        "vLLM 기본값. 이미 적용됨 (별도 조치 불필요)"),
        ("INT8 / 4bit 양자화", f"peak {peak_mb:.0f}MB → 약 {peak_mb*0.55:.0f}MB 예상. --quantization awq/gptq"),
        ("이미지 해상도 축소",  f"VIDEO_IMAGE_WIDTH={AIConfig().video_image_width} → 320 으로 줄이면 입력 토큰 감소"),
        ("영상 프레임 수 축소", f"VIDEO_MAX_FRAMES={AIConfig().video_max_frames} → 8~12 로 줄이면 다중 이미지 토큰 감소"),
        ("배치 크기 축소",      "AI_CONCURRENCY=1 로 낮추면 동시 요청 VRAM 충돌 방지"),
        ("KV cache 제한",       "vLLM --gpu-memory-utilization 0.80 으로 제한 (기본 0.90)"),
        ("모델 unload",         "현재 구조상 해당 없음 -vLLM 서버가 상시 모델 유지"),
        ("torch.cuda.empty_cache()", "vLLM 내부에서 자동 처리 -앱에서 직접 호출 불필요"),
        ("CPU offload",         "vLLM --cpu-offload-gb N 옵션으로 일부 레이어 CPU 이동 가능"),
        ("attention 최적화",    "vLLM --enable-chunked-prefill 또는 FlashAttention (자동 감지)"),
    ]
    for name, note in optimizations:
        print(f"  • {name:<22} : {note}")

    print("\n" + "=" * 70)
    peak_gb = peak_mb / 1024
    recommend_gb = peak_mb * 1.15 / 1024
    print(
        f"결론: 이 프로그램은 로컬 Docker 환경에서 실행되며, "
        f"테스트 기준 최대 GPU VRAM 사용량은 약 {peak_gb:.1f} GB이고 "
        f"{recommend_gb:.0f} GB 이상의 GPU를 권장합니다."
    )
    print("=" * 70)

    results = {
        "gpu_name": gpu_name,
        "total_vram_mb": total_mb,
        "idle_vram_mb": idle_mb,
        "text_avg_mb": text_avg,
        "image_avg_mb": image_avg,
        "video_avg_mb": video_avg,
        "peak_mb": peak_mb,
        "final_mb": final_mb,
        "leak_delta_mb": leak_delta,
        "leak_detected": leak_detected,
        "snapshots": [
            {"label": s.label, "used_mb": s.used_mb, "total_mb": s.total_mb,
             "process_mb": s.process_mb, "source": s.source}
            for s in snapshots
        ],
    }

    out_path = Path(__file__).parent / "vram_benchmark_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n전체 결과 저장: {out_path}")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Clasq VRAM 측정 벤치마크")
    parser.add_argument("--base-url", default=os.getenv("AI_BASE_URL", "http://127.0.0.1:8100/v1"))
    parser.add_argument("--model", default=os.getenv("AI_MODEL", "qwen3-vl-8b"))
    parser.add_argument("--image", default=None, help="테스트용 이미지 경로 (없으면 합성 이미지 사용)")
    parser.add_argument("--video", default=None, help="실제 동영상 파일 경로 (선택)")
    parser.add_argument("--repeat", type=int, default=3, help="단일 추론 반복 횟수 (기본: 3)")
    parser.add_argument("--device", type=int, default=0, help="GPU 장치 인덱스 (기본: 0)")
    args = parser.parse_args()

    run_benchmark(
        base_url=args.base_url,
        model=args.model,
        image_path=args.image,
        video_path=args.video,
        repeat=args.repeat,
        device_index=args.device,
    )


if __name__ == "__main__":
    main()
