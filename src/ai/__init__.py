"""Clasq AI inference layer.

AI_MODE=llama_server (기본값): 로컬 llama-server + Qwen3-VL 사용
AI_MODE=ollama              : 기존 Ollama 호환 경로 (src/utils/config.py 설정 사용)
AI_MODE=remote              : 원격 AI 서버 (AI_BASE_URL 환경변수로 지정)

무거운 모듈(PIL, requests)은 여기서 자동 import하지 않는다.
필요한 클래스는 각 모듈에서 직접 import해서 사용한다.
"""

from .config import AIConfig, get_ai_mode

__all__ = ["AIConfig", "get_ai_mode"]
