# -*- mode: python ; coding: utf-8 -*-
"""
Clasq Windows PyInstaller 스펙 파일 (one-dir 방식)

빌드:
  pyinstaller clasq.spec

산출물:
  dist/Clasq/
    Clasq.exe
    runtime/            ← llama-server + DLL + ffmpeg (앱과 함께 배포)
    assets/             ← QSS, 아이콘
    _internal/          ← PyInstaller 내부 (Python, 패키지)
"""

import os
from pathlib import Path

# ── 경로 ──────────────────────────────────────────────────────────────────
LLAMA_BIN = r"C:\llama-cpp\bin"
FFMPEG_EXE = (
    r"C:\Users\USER1\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe"
)

# ── runtime/ 에 번들할 바이너리 목록 ─────────────────────────────────────
#   프로세스 로드 기준으로 확인한 필수 파일만 포함
_LLAMA_REQUIRED = [
    "llama-server.exe",
    "llama-server-impl.dll",
    "mtmd.dll",
    "llama.dll",
    "llama-common.dll",
    "ggml.dll",
    "ggml-base.dll",
    "ggml-cuda.dll",
    "ggml-rpc.dll",
    "libomp.dll",
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudart64_12.dll",
]
_LLAMA_CPU_FALLBACK = [
    "ggml-cpu-alderlake.dll",
    "ggml-cpu-cannonlake.dll",
    "ggml-cpu-cascadelake.dll",
    "ggml-cpu-cooperlake.dll",
    "ggml-cpu-haswell.dll",
    "ggml-cpu-icelake.dll",
    "ggml-cpu-ivybridge.dll",
    "ggml-cpu-piledriver.dll",
    "ggml-cpu-sandybridge.dll",
    "ggml-cpu-sapphirerapids.dll",
    "ggml-cpu-skylakex.dll",
    "ggml-cpu-sse42.dll",
    "ggml-cpu-x64.dll",
    "ggml-cpu-zen4.dll",
]

runtime_binaries = []
for name in _LLAMA_REQUIRED + _LLAMA_CPU_FALLBACK:
    src = os.path.join(LLAMA_BIN, name)
    if os.path.isfile(src):
        runtime_binaries.append((src, "runtime"))

if os.path.isfile(FFMPEG_EXE):
    runtime_binaries.append((FFMPEG_EXE, "runtime"))

# ── 분석 ──────────────────────────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=runtime_binaries,
    datas=[
        # 정적 리소스 (QSS, 아이콘)
        ("assets", "assets"),
    ],
    hiddenimports=[
        # PySide6 플러그인
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "PySide6.QtXml",
        # 동적 import (lazy)
        "src.ai.hardware_detector",
        "src.ai.runtime_profile",
        "src.ai.model_downloader",
        "src.ai.server_manager",
        "src.ai.startup_worker",
        "src.recommendation.scope_policy",
        "src.recommendation.service",
        "src.recommendation.qwen_reranker",
        "src.recommendation.retriever",
        "src.recommendation.profile_builder",
        "src.recommendation.folder_repository",
        "src.recommendation.models",
        "src.recommendation.family",
        "src.utils.local_text_index",
        "src.utils.search_snapshot",
        # 문서 파싱 라이브러리
        "pypdf",
        "docx",
        "openpyxl",
        "pptx",
        "olefile",
        # 이미지
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        # 네트워크
        "requests",
        "urllib3",
        "certifi",
        "charset_normalizer",
        "idna",
    ],
    excludes=[
        # 배포에 불필요한 대형 패키지
        "torch",
        "torchvision",
        "easyocr",
        "whisper",
        "cv2",
        "numpy",
        "scipy",
        "sklearn",
        "matplotlib",
        "IPython",
        "jupyter",
        "notebook",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Clasq",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # UPX 압축 비활성 (백신 오탐 방지)
    console=False,                # 콘솔 창 없음 (windowed 앱)
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,                    # TODO: 아이콘 추가 시 "assets/clasq.ico" 로 설정
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Clasq",
)
