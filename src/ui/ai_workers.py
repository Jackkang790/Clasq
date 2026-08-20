"""Background workers for file analysis and file-scoped AI questions."""

from __future__ import annotations

import logging
from pathlib import Path
import threading
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from src.ai.qwen_client import AIConnectionError, AIResponseError, AITimeoutError
from src.ai.video_analyzer import FFmpegNotFoundError

if TYPE_CHECKING:
    from src.ai.image_analyzer import ImageAnalyzer
    from src.ai.video_analyzer import VideoAnalyzer
    from src.utils.main_processor import MainProcessor


logger = logging.getLogger(__name__)


class AIServiceContainer:
    """Lazily build and then reuse the non-QObject backend service graph."""

    def __init__(self, main_processor=None, image_analyzer=None, video_analyzer=None):
        self.main_processor = main_processor
        facade = getattr(main_processor, "analyzer", None)
        self.image_analyzer = image_analyzer or getattr(facade, "image_analyzer", None)
        self.video_analyzer = video_analyzer or getattr(facade, "video_analyzer", None)
        self._lock = threading.Lock()

    def get(self):
        if self.main_processor and self.image_analyzer and self.video_analyzer:
            return self.main_processor, self.image_analyzer, self.video_analyzer
        with self._lock:
            if not (self.main_processor and self.image_analyzer and self.video_analyzer):
                # These imports include document/Whisper dependencies, so keep
                # them off the GUI startup path and initialize in the worker.
                from src.utils.file_pipeline import FileAnalyzer, TextExtractor
                from src.utils.main_processor import MainProcessor
                from src.utils.query_parser import SearchQueryParser

                analyzer = FileAnalyzer()
                self.main_processor = MainProcessor(
                    TextExtractor(), analyzer, SearchQueryParser(client=analyzer.client)
                )
                self.image_analyzer = analyzer.image_analyzer
                self.video_analyzer = analyzer.video_analyzer
        return self.main_processor, self.image_analyzer, self.video_analyzer


class AIFileWorker(QThread):
    """Run one analyzer operation without touching GUI objects."""

    succeeded = Signal(object)
    failed = Signal(str)
    status = Signal(str)

    ANALYZE_FILE = "analyze_file"
    ASK_IMAGE = "ask_image"
    ASK_VIDEO = "ask_video"

    def __init__(
        self,
        operation: str,
        file_path: str,
        *,
        services: AIServiceContainer,
        user_prompt: Optional[str] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.operation = operation
        self.file_path = file_path
        self.user_prompt = user_prompt or ""
        self.services = services

    def run(self) -> None:
        try:
            main_processor, image_analyzer, video_analyzer = self.services.get()
            if self.operation == self.ANALYZE_FILE:
                extension = Path(self.file_path).suffix.lower()
                if extension in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"}:
                    self.status.emit("이미지를 분석하고 있습니다...")
                elif extension in {".mp4", ".mkv", ".avi"}:
                    self.status.emit("영상에서 주요 장면을 추출하고 있습니다...")
                else:
                    self.status.emit("파일 내용을 분석하고 있습니다...")
                result = main_processor.process_file_upload(self.file_path)
            elif self.operation == self.ASK_IMAGE:
                self.status.emit("이미지에 대한 답변을 생성하고 있습니다...")
                result = image_analyzer.ask_image(self.file_path, self.user_prompt)
            elif self.operation == self.ASK_VIDEO:
                self.status.emit("영상 대표 장면을 확인하고 답변을 생성하고 있습니다...")
                result = video_analyzer.ask_video(self.file_path, self.user_prompt)
            else:
                raise ValueError(f"지원하지 않는 AI 작업입니다: {self.operation}")
            self.succeeded.emit(result)
        except Exception as exc:
            logger.exception("AI file worker failed: %s", self.operation)
            self.failed.emit(self._friendly_error(exc))

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        if isinstance(exc, AIConnectionError):
            return "AI 서버에 연결할 수 없습니다. 서버 또는 SSH 터널을 확인해주세요."
        if isinstance(exc, AITimeoutError):
            return "AI 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."
        if isinstance(exc, FFmpegNotFoundError):
            return "영상 분석에 필요한 FFmpeg를 찾지 못했습니다."
        if isinstance(exc, FileNotFoundError):
            return "첨부한 파일을 찾을 수 없습니다."
        if isinstance(exc, PermissionError):
            return "첨부한 파일에 접근할 권한이 없습니다."
        if isinstance(exc, AIResponseError):
            return "AI 응답 형식을 처리할 수 없습니다."
        return f"AI 처리 중 오류가 발생했습니다: {exc}"
