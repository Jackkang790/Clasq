# -*- mode: python ; coding: utf-8 -*-
"""Reproducible Windows one-dir build for Clasq.

Set CLASQ_LLAMA_RUNTIME when llama.cpp is installed in a non-default location.
FFmpeg is selected through its pinned manifest and verified staging workflow.
GGUF files are deliberately not bundled; the product downloader stores them in
the user's LOCALAPPDATA directory.
"""
import os
from pathlib import Path

from scripts.filter_runtime_binaries import exclude_verified_root_duplicates
from scripts.filter_qt_runtime import exclude_verified_unused_qt_runtime
from scripts.filter_pillow_runtime import exclude_verified_unused_pillow_runtime
from scripts.filter_qtpdf_runtime import exclude_verified_unused_qtpdf_runtime
from scripts.ffmpeg_artifact import resolve_build_ffmpeg

project = Path(SPEC).resolve().parent
runtime_source = Path(os.environ.get("CLASQ_LLAMA_RUNTIME", r"C:\llama-cpp\bin"))
ffmpeg_source = resolve_build_ffmpeg(project)

required_runtime = [
    "llama-server.exe", "llama-server-impl.dll", "llama-common.dll",
    "llama.dll", "mtmd.dll", "ggml.dll", "ggml-base.dll", "ggml-cuda.dll",
    "ggml-rpc.dll", "libomp.dll", "cudart64_12.dll", "cublas64_12.dll",
    "cublasLt64_12.dll",
]
cpu_backends = sorted(runtime_source.glob("ggml-cpu-*.dll"))
missing = [name for name in required_runtime if not (runtime_source / name).is_file()]
if missing:
    raise SystemExit(f"Missing llama.cpp runtime files in {runtime_source}: {missing}")
binaries = [(str(runtime_source / name), "runtime") for name in required_runtime]
binaries += [(str(path), "runtime") for path in cpu_backends]
binaries.append((str(ffmpeg_source), "runtime"))

compliance_files = [
    (str(project / "LICENSE"), "."),
    (str(project / "THIRD_PARTY_NOTICES.md"), "."),
    (str(project / "FFMPEG_SOURCE_INFO.txt"), "."),
    (str(project / "THIRD_PARTY_LICENSES"), "THIRD_PARTY_LICENSES"),
]

hiddenimports = [
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtXml",
    "src.ai.hardware_detector", "src.ai.runtime_profile",
    "src.ai.model_downloader", "src.ai.server_manager", "src.ai.startup_worker",
    "src.recommendation.scope_policy", "src.recommendation.service",
    "src.recommendation.qwen_reranker", "src.recommendation.retriever",
    "src.recommendation.profile_builder", "src.recommendation.folder_repository",
    "src.recommendation.models", "src.recommendation.family",
    "src.utils.local_text_index", "src.utils.search_snapshot",
    "pypdf", "docx", "openpyxl", "pptx", "olefile", "PIL.ImageDraw",
]

excluded = [
    "torch", "torchvision", "easyocr", "whisper", "cv2", "numpy", "scipy",
    "sklearn", "matplotlib", "IPython", "jupyter", "notebook",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
]

a = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=binaries,
    datas=[(str(project / "assets"), "assets"), *compliance_files],
    hiddenimports=hiddenimports,
    excludes=excluded,
    noarchive=False,
)
# Analysis follows PE imports and adds imported runtime DLLs again at the
# one-dir root.  Keep the validated runtime layout and remove only reviewed,
# byte-identical root entries.  Any missing/mismatched counterpart fails build.
a.binaries, removed_root_duplicates = exclude_verified_root_duplicates(a.binaries)
for duplicate in removed_root_duplicates:
    print(
        "Excluded verified root duplicate: "
        f"{duplicate['relative_path']} ({duplicate['size']} bytes, "
        f"sha256={duplicate['sha256']})"
    )
a.binaries, removed_qt_runtime = exclude_verified_unused_qt_runtime(a.binaries)
for component in removed_qt_runtime:
    print(
        "Excluded verified unused Qt runtime: "
        f"{component['relative_path']} ({component['size']} bytes, "
        f"sha256={component['sha256']})"
    )
a.binaries, removed_pillow_runtime = exclude_verified_unused_pillow_runtime(a.binaries)
for component in removed_pillow_runtime:
    print(
        "Excluded verified unused Pillow runtime: "
        f"{component['relative_path']} ({component['size']} bytes, "
        f"sha256={component['sha256']})"
    )
a.binaries, removed_qtpdf_runtime = exclude_verified_unused_qtpdf_runtime(a.binaries)
for component in removed_qtpdf_runtime:
    print(
        "Excluded verified unused QtPdf runtime: "
        f"{component['relative_path']} ({component['size']} bytes, "
        f"sha256={component['sha256']})"
    )
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Clasq",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    console=False, disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas, strip=False, upx=True, name="Clasq",
)
