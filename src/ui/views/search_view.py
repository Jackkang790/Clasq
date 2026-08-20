import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

# 신규 위젯 임포트
from src.ui.widgets.fileupload_view import FileUploadView
from src.ui.ai_workers import AIFileWorker, AIServiceContainer
from src.utils.search_engine import SearchEngine

# 문장에서 확장자 필터를 뽑아낼 때 쓰는 후보 목록.
# SearchEngine.STOP_WORDS와 겹치는 확장자 표기를 그대로 재사용한다.
_EXTENSION_CANDIDATES = [
    "pdf", "hwp", "hwpx", "docx", "xlsx", "ppt", "pptx",
    "png", "jpg", "jpeg", "gif", "mp3", "mp4",
]

_FILE_TYPE_ALIASES = {
    "피피티": ["ppt", "pptx"],
    "ppt": ["ppt", "pptx"],
    "파워포인트": ["ppt", "pptx"],
    "프레젠테이션": ["ppt", "pptx"],
}

_QUERY_STOP_WORDS = {
    "관련", "관련된", "찾아줘", "찾아주세요", "검색", "검색해줘",
    "보여줘", "보여주세요", "알려줘", "파일", "문서",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a"}


class SearchView(QWidget):

    def __init__(
        self,
        parent=None,
        search_engine=None,
        query_parser=None,
        main_processor=None,
        image_analyzer=None,
        video_analyzer=None,
    ):
        """
        search_engine: SearchEngine 인스턴스를 밖에서 주입할 수 있다.
                       (DB 경로를 다르게 쓰거나 테스트용 mock을 넣고 싶을 때)
        query_parser:  자연어 문장(str) -> parsed_data(dict)로 바꾸는 함수.
                       REQ-011의 실제 LLM 의도 파서가 준비되면 이 인자로 갈아끼우면 된다.
                       시그니처: (text: str) -> dict  (SearchEngine.process_query_result가 먹는 형태)
        """
        super().__init__(parent)
        self.setAcceptDrops(True)

        self.search_engine = search_engine or SearchEngine()
        self._query_parser = query_parser or self._parse_natural_query

        self._ai_services = AIServiceContainer(
            main_processor=main_processor,
            image_analyzer=image_analyzer,
            video_analyzer=video_analyzer,
        )

        self.current_file_path = None
        self.current_file_kind = None
        self._ai_worker = None
        self._ai_busy = False

        self.init_ui()

    def init_ui(self):
        self.setStyleSheet("""
            QLabel#mainTitle {
                font-size: 26px;
                font-weight: bold;
                color: #212529;
                margin-bottom: 20px;
            }

            QLineEdit.searchInput {
                border: 1.5px solid #CED4DA;
                border-radius: 20px;
                padding: 8px 18px;
                font-size: 14px;
                background-color: #FFFFFF;
                color: #212529;
            }
            QLineEdit.searchInput:focus {
                border: 1.5px solid #6C5CE7;
            }

            QScrollArea {
                border: none;
                background-color: transparent;
            }

            QLabel.userBubble {
                background-color: #E9ECEF;
                color: #212529;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 14px;
            }

            QLabel.aiBubble {
                background-color: #FFFFFF;
                color: #212529;
                border: 1px solid #E0E0E0;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 14px;
            }

            QLabel.resultBubble {
                background-color: #F5F4FF;
                color: #212529;
                border: 1px solid #DCD6FF;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 13px;
            }

            QLabel.errorBubble {
                background-color: #FDEDEC;
                color: #C0392B;
                border: 1px solid #F5B7B1;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 14px;
            }

            QPushButton.sendBtn {
                background-color: #6C5CE7;
                color: white;
                border: none;
                border-radius: 18px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton.sendBtn:hover {
                background-color: #5A4AD1;
            }
        """)

        self.stacked_layout = QStackedLayout(self)

        # 1. 초기 화면 (Index 0)
        self.init_widget = QWidget()
        init_layout = QVBoxLayout(self.init_widget)
        init_layout.setAlignment(Qt.AlignCenter)

        title = QLabel("무엇을 도와드릴까요?")
        title.setObjectName("mainTitle")
        title.setAlignment(Qt.AlignCenter)

        self.initial_input = QLineEdit()
        self.initial_input.setProperty("class", "searchInput")
        self.initial_input.setFixedWidth(420)
        self.initial_input.setFixedHeight(44)
        self.initial_input.setPlaceholderText("무엇이든 입력해보세요!")
        self.initial_input.returnPressed.connect(self.on_initial_search)

        init_layout.addWidget(title)
        init_layout.addWidget(self.initial_input)

        # 2. 채팅 화면 (Index 1)
        self.chat_widget = QWidget()
        chat_main_layout = QVBoxLayout(self.chat_widget)
        chat_main_layout.setContentsMargins(20, 20, 20, 20)

        # 스크롤 영역
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(10, 10, 10, 10)
        self.chat_layout.setSpacing(12)
        self.chat_layout.addStretch()

        self.scroll_area.setWidget(self.chat_container)
        chat_main_layout.addWidget(self.scroll_area, 1)

        # ★ 기존 bottom_input_layout 대신 신규 위젯 배치
        self.chat_input_widget = FileUploadView()
        self.chat_input_widget.message_submitted.connect(self.on_chat_search)
        self.chat_input_widget.file_attached.connect(self.on_file_attached)

        chat_main_layout.addWidget(self.chat_input_widget)

        self.stacked_layout.addWidget(self.init_widget)  # Index 0
        self.stacked_layout.addWidget(self.chat_widget)  # Index 1
        self.stacked_layout.setCurrentIndex(0)

    def on_initial_search(self):
        query = self.initial_input.text().strip()
        if not query:
            return

        self.stacked_layout.setCurrentIndex(1)
        self.process_query(query)

    def on_chat_search(self, query: str):
        self.process_query(query)

    def on_file_attached(self, file_path: str):
        if self._ai_busy:
            self.add_message("⚠️ 현재 AI 작업이 끝난 후 다른 파일을 첨부해주세요.", is_user=False, kind="error")
            return

        normalized_path = os.path.abspath(os.path.normpath(file_path))
        if not os.path.isfile(normalized_path):
            self.add_message("⚠️ 첨부한 파일을 찾을 수 없습니다.", is_user=False, kind="error")
            return

        file_kind = self._classify_file(normalized_path)

        if self.stacked_layout.currentIndex() == 0:
            self.stacked_layout.setCurrentIndex(1)

        self.current_file_path = normalized_path
        self.current_file_kind = file_kind
        self.chat_input_widget.input_field.setPlaceholderText(
            f"{Path(normalized_path).name}에 대해 물어보세요"
        )
        self.add_message(f"📎 {Path(normalized_path).name}", is_user=True)
        self._start_ai_worker(AIFileWorker.ANALYZE_FILE, normalized_path)

    # -----------------------------------------------------------------
    # 실제 검색 연결부: 자연어 -> parser -> SearchEngine -> 채팅 UI 렌더링
    # -----------------------------------------------------------------
    def process_query(self, query: str):
        self.add_message(query, is_user=True)

        if self._ai_busy:
            self.add_message("⚠️ 현재 작업이 끝난 후 다시 질문해주세요.", is_user=False, kind="error")
            return

        if self.current_file_path and self.current_file_kind == "image":
            self._start_ai_worker(
                AIFileWorker.ASK_IMAGE,
                self.current_file_path,
                user_prompt=query,
            )
            return
        if self.current_file_path and self.current_file_kind == "video":
            self._start_ai_worker(
                AIFileWorker.ASK_VIDEO,
                self.current_file_path,
                user_prompt=query,
            )
            return
        if self.current_file_path and self.current_file_kind in {"audio", "document"}:
            self.add_message(
                "현재 첨부 파일 후속 질문은 이미지와 영상만 지원합니다. 문서와 오디오 질문은 향후 지원 예정입니다.",
                is_user=False,
            )
            return

        parsed_data = self._query_parser(query)

        try:
            result = self.search_engine.process_query_result(parsed_data)
        except Exception as exc:
            # DB 파일/테이블이 아직 없는 초기 상태 등을 대비한 방어 처리.
            # (REQ-006과 동일하게, 백엔드 연결 실패 시 앱이 죽지 않고 안내만 하도록.)
            self.add_message(f"⚠️ 검색 중 오류가 발생했습니다: {exc}", is_user=False, kind="error")
            return

        action = result.get("action")
        message = result.get("message", "")
        data = result.get("data", [])

        if action == "UPDATE_TABLE":
            self.add_message(message, is_user=False)
            self._render_search_results(data)
        elif action == "SHOW_CHAT":
            self.add_message(message, is_user=False)
        else:  # ERROR
            self.add_message(f"⚠️ {message}", is_user=False, kind="error")

    @staticmethod
    def _classify_file(file_path: str) -> str:
        extension = Path(file_path).suffix.lower()
        if extension in IMAGE_EXTENSIONS:
            return "image"
        if extension in VIDEO_EXTENSIONS:
            return "video"
        if extension in AUDIO_EXTENSIONS:
            return "audio"
        return "document"

    def _start_ai_worker(self, operation: str, file_path: str, user_prompt: str = ""):
        if self._ai_busy:
            return
        self._set_ai_busy(True)
        worker = AIFileWorker(
            operation,
            file_path,
            services=self._ai_services,
            user_prompt=user_prompt,
            parent=self,
        )
        self._ai_worker = worker
        worker.status.connect(lambda message, active=worker: self._on_ai_status(active, message))
        worker.succeeded.connect(lambda result, active=worker: self._on_ai_succeeded(active, result))
        worker.failed.connect(lambda message, active=worker: self._on_ai_failed(active, message))
        worker.finished.connect(lambda active=worker: self._on_ai_finished(active))
        worker.start()

    def _on_ai_status(self, worker, message: str):
        if worker is self._ai_worker:
            self.add_message(message, is_user=False)

    def _on_ai_succeeded(self, worker, result):
        if worker is not self._ai_worker:
            return
        if worker.operation in {AIFileWorker.ASK_IMAGE, AIFileWorker.ASK_VIDEO}:
            answer = str(result).strip()
            if answer:
                self.add_message(answer, is_user=False)
            else:
                self.add_message("⚠️ AI 응답이 비어 있습니다.", is_user=False, kind="error")
            return

        error = self._pipeline_error(result)
        if error:
            self.add_message(f"⚠️ {error}", is_user=False, kind="error")
            return
        data = result.get("payload", {}).get("data", {})
        self.add_message(self._format_analysis_result(worker.file_path, data), is_user=False, kind="result")

    def _on_ai_failed(self, worker, message: str):
        if worker is self._ai_worker:
            self.add_message(f"⚠️ {message}", is_user=False, kind="error")

    def _on_ai_finished(self, worker):
        if worker is self._ai_worker:
            self._ai_worker = None
            self._set_ai_busy(False)
        worker.deleteLater()

    def _set_ai_busy(self, busy: bool):
        self._ai_busy = busy
        self.chat_input_widget.set_busy(busy)
        self.initial_input.setEnabled(not busy)

    @staticmethod
    def _pipeline_error(result) -> str:
        if not isinstance(result, dict):
            return "파일 분석 결과 형식이 올바르지 않습니다."
        if result.get("response_type") == "ERROR":
            return result.get("payload", {}).get("message", "파일 분석에 실패했습니다.")
        data = result.get("payload", {}).get("data", {})
        if data.get("status") == "FAILED":
            return data.get("error") or data.get("description") or "파일 분석에 실패했습니다."
        return ""

    @classmethod
    def _format_analysis_result(cls, file_path: str, data: dict) -> str:
        metadata = data.get("metadata", {})
        display_name = data.get("display_name") or Path(file_path).stem
        description = data.get("description") or metadata.get("summary", "")
        tags = data.get("tags") or []
        kind = cls._classify_file(file_path)

        if kind == "image":
            lines = ["이미지 분석 완료", "", f"제목: {display_name}"]
            if description:
                lines.append(f"설명: {description}")
            if tags:
                lines.append(f"태그: {', '.join(map(str, tags))}")
            ocr_text = metadata.get("ocr_text", "")
            if ocr_text:
                lines.append(f"읽힌 글자: {ocr_text}")
            return "\n".join(lines)

        if kind == "video":
            summary = metadata.get("summary") or description
            lines = ["영상 분석 완료", ""]
            if summary:
                lines.extend(["요약:", str(summary)])
            applications = metadata.get("applications") or []
            if applications:
                lines.extend(["", "주요 프로그램:"])
                lines.extend(f"- {app}" for app in applications[:8])
            timeline = metadata.get("timeline") or []
            if timeline:
                lines.extend(["", "주요 장면:"])
                for item in timeline[:5]:
                    if isinstance(item, dict):
                        time = item.get("time", "")
                        scene = item.get("scene", "")
                        lines.append(f"- {time} {scene}".strip())
            return "\n".join(lines)

        label = "오디오" if kind == "audio" else "문서"
        lines = [f"{label} 분석 완료", "", f"제목: {display_name}"]
        if description:
            lines.append(f"설명: {description}")
        if tags:
            lines.append(f"태그: {', '.join(map(str, tags))}")
        lines.append("후속 자연어 질문은 현재 이미지와 영상만 지원합니다.")
        return "\n".join(lines)

    def _render_search_results(self, rows):
        """
        SearchEngine이 돌려주는 (id, file_name, file_path, ai_comment, category) 튜플 목록을
        채팅 버블 형태로 하나씩 표시한다.
        """
        MAX_SHOWN = 10
        for row in rows[:MAX_SHOWN]:
            _id, file_name, file_path, ai_comment, category = row
            lines = [f"📄 {file_name}"]
            result_meta = self.search_engine.get_result_metadata(file_path) \
                if hasattr(self.search_engine, "get_result_metadata") else {}
            if result_meta.get("analysis_status") == "pending":
                lines.append("AI 분석 전")
            if category:
                lines.append(f"분류: {category}")
            if file_path:
                lines.append(f"경로: {file_path}")
            if ai_comment:
                lines.append(f"메모: {ai_comment}")
            self.add_message("\n".join(lines), is_user=False, kind="result")

        remaining = len(rows) - MAX_SHOWN
        if remaining > 0:
            self.add_message(f"...외 {remaining}건 더 있습니다.", is_user=False)

    # -----------------------------------------------------------------
    # 임시 자연어 파서 (TODO: REQ-011 LLM 의도 파서로 교체 예정)
    # -----------------------------------------------------------------
    def _parse_natural_query(self, text: str) -> dict:
        """
        아주 단순한 규칙 기반 임시 파서.
        - 문장에서 알려진 확장자 단어를 뽑아 target_extension으로 분리
        - 나머지 단어는 전부 query_keywords로 넘겨서 SearchEngine의
          불용어 제거/동의어 확장/AND->OR 폴백 로직이 실제 필터링을 하도록 위임한다.
        - 항상 "@검색"으로 라우팅한다 (자유 대화 의도 분류는 LLM 파서가 붙기 전까진 생략).
        """
        words = text.strip().split()
        extensions = []
        keywords = []

        for w in words:
            w_clean = w.strip(".,!?").lower()
            if w_clean == "pptx":
                extensions.append("pptx")
            elif w_clean in _FILE_TYPE_ALIASES:
                extensions.extend(_FILE_TYPE_ALIASES[w_clean])
            elif w_clean in _EXTENSION_CANDIDATES:
                extensions.append(w_clean)
            elif w_clean not in _QUERY_STOP_WORDS:
                keywords.append(w)

        return {
            "@TYPE": "@검색",
            "query_keywords": keywords,
            "target_extension": list(dict.fromkeys(extensions)),
        }

    # -----------------------------------------------------------------
    # 채팅 버블 렌더링
    # -----------------------------------------------------------------
    def add_message(self, text: str, is_user: bool = True, kind: str = "normal"):
        """
        kind: "normal" | "result" | "error"
        - normal: 기존 userBubble/aiBubble
        - result: 검색 결과 1건을 나타내는 연보라색 버블
        - error : 에러/경고를 나타내는 빨간 톤 버블
        """
        row_layout = QHBoxLayout()
        bubble = QLabel(text)
        bubble.setWordWrap(True)

        if is_user:
            bubble.setProperty("class", "userBubble")
            row_layout.addStretch()
            row_layout.addWidget(bubble)
        else:
            if kind == "result":
                bubble.setProperty("class", "resultBubble")
            elif kind == "error":
                bubble.setProperty("class", "errorBubble")
            else:
                bubble.setProperty("class", "aiBubble")
            row_layout.addWidget(bubble)
            row_layout.addStretch()

        bubble.style().unpolish(bubble)
        bubble.style().polish(bubble)

        self.chat_layout.insertLayout(self.chat_layout.count() - 1, row_layout)
        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        QApplication.processEvents()
        v_bar = self.scroll_area.verticalScrollBar()
        v_bar.setValue(v_bar.maximum())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            self.on_file_attached(file_path)
