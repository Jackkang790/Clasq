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
        except AITimeoutError as exc:
            logger.warning("AI 응답 시간 초과: %s", exc)
            self.failed.emit("로컬 AI 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.")
        except AIConnectionError as exc:
            logger.warning("AI 연결 실패: %s", exc)
            self.failed.emit(f"AI 서버에 연결할 수 없습니다: {exc}")
        except AIResponseError as exc:
            logger.warning("AI 응답 오류: %s", exc)
            self.failed.emit(f"AI 응답 처리 실패: {exc}")
        except FFmpegNotFoundError:
            self.failed.emit("FFmpeg를 찾을 수 없습니다. 영상 분석을 사용하려면 FFmpeg가 필요합니다.")
        except Exception as exc:
            logger.exception("AIFileWorker 예상치 못한 오류")
            self.failed.emit(str(exc))
