"""앱 경로 중앙화 모듈.

개발 환경(python main.py)과 PyInstaller 번들(Clasq.exe) 양쪽에서
동일하게 동작하는 경로 헬퍼를 제공한다.

PyInstaller one-dir 번들 구조:
  dist/Clasq/
    Clasq.exe                    ← sys.executable
    runtime/                     ← llama-server, DLL, ffmpeg
    assets/
      styles/
        light.qss
        icons/
    _internal/                   ← Python + 패키지 (PyInstaller 관리)

개발 환경 구조:
  Z:/sjb/Clasq/
    main.py
    runtime/
    assets/
    src/
      utils/
        app_paths.py             ← __file__
"""
from __future__ import annotations

import os
import sys


def _internal_dir() -> str:
    """PyInstaller 6.x one-dir 번들에서 리소스가 들어 있는 _internal/ 경로.

    - PyInstaller 번들: sys._MEIPASS  (== dist/Clasq/_internal/)
    - 개발 환경: 프로젝트 루트 (app_paths.py 기준 두 단계 위)
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", ".."))


def app_base_dir() -> str:
    """앱 exe 가 있는 디렉터리 (일반 사용자 데이터 경로 계산에 사용).

    - PyInstaller 번들: sys.executable 의 부모 (dist/Clasq/)
    - 개발 환경: 프로젝트 루트
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", ".."))


def runtime_dir() -> str:
    """llama-server.exe / DLL / ffmpeg.exe 위치.

    PyInstaller 6.x one-dir 번들에서는 _internal/runtime/ 에 있음.
    """
    return os.path.join(_internal_dir(), "runtime")


def assets_dir() -> str:
    """QSS, 아이콘 등 정적 리소스 위치.

    PyInstaller 6.x one-dir 번들에서는 _internal/assets/ 에 있음.
    """
    return os.path.join(_internal_dir(), "assets")
