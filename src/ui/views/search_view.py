import os
import subprocess
import sys

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,          # ← 추가
    QGridLayout,      # ← 추가
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

# 신규 위젯 임포트
from src.ui.widgets.fileupload_view import FileUploadView
from src.ui.ai_workers import AIFileWorker, AIServiceContainer
from src.utils.search_engine import SearchEngine
from src.utils.workers import FolderScanAndTagWorker

# 문장에서 확장자 필터를 뽑아낼 때 쓰는 후보 목록.
# SearchEngine.STOP_WORDS와 겹치는 확장자 표기를 그대로 재사용한다.
_EXTENSION_CANDIDATES = [
    "pdf", "hwp", "hwpx", "docx", "xlsx", "pptx", "txt", "csv", "json", "xml", "yaml", "yml", "html", "htm", "md", "markdown",
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif", "mp3", "mp4",
]

class QueryProcessWorker(QThread):
    """Ollama 기반 자연어 검색을 UI 스레드 밖에서 처리합니다."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, core, query, parent=None):
        super().__init__(parent)
        self.core = core
        self.query = query

    def run(self):
        try:
            self.finished.emit(self.core.process_user_query(self.query))
        except Exception as exc:
            self.error.emit(str(exc))

# src/ui/views/search_view.py
class _FileResultCard(QFrame):
    """검색 결과 1건을 나타내는 클릭 가능한 카드 (아이콘 + 파일명)"""
    clicked = Signal(str)  # file_path를 실어서 emit

    def __init__(self, file_name: str, file_path: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.setObjectName("fileResultCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(150, 130)
        if tooltip:
            self.setToolTip(tooltip)

        self.setStyleSheet("""
            QFrame#fileResultCard {
                background-color: #FFFFFF;
                border: 1px solid #E4E6EF;
                border-radius: 14px;
            }
            QFrame#fileResultCard:hover {
                background-color: #F5F4FF;
                border: 1px solid #DCD6FF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(10)

        icon_label = QLabel("📄")
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet("font-size: 30px; color: #B9A9FF; background: transparent; border: none;")
        layout.addWidget(icon_label)

        name_label = QLabel(file_name)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #4B4B63; background: transparent; border: none;")
        layout.addWidget(name_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.file_path)
        super().mousePressEvent(event)

class SearchView(QWidget):
    def __init__(self, core=None, parent=None, search_engine=None, query_parser=None, refresh_manager=None):
        super().__init__(parent)
        self.core = core
        self.refresh_manager = refresh_manager

        """
        search_engine: SearchEngine 인스턴스를 밖에서 주입할 수 있다.
                       (DB 경로를 다르게 쓰거나 테스트용 mock을 넣고 싶을 때)
        query_parser:  자연어 문장(str) -> parsed_data(dict)로 바꾸는 함수.
                       REQ-011의 실제 LLM 의도 파서가 준비되면 이 인자로 갈아끼우면 된다.
                       시그니처: (text: str) -> dict  (SearchEngine.process_query_result가 먹는 형태)
        """

        self._loading_widget = None
        self._loading_bubble = None
        self._loading_timer = None
        self._loading_dot_count = 0
        self._attached_file_path = None
        self._attached_analysis = None
        self._ai_services = None

        self.setAcceptDrops(True)

        self.search_engine = search_engine or (
            core.search_engine if core is not None else SearchEngine()
        )
        self._query_parser = query_parser or (
            core.query_parser.parse_user_query
            if core is not None else self._parse_natural_query
        )

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

            QScrollBar:vertical {
                border: none;
                background-color: transparent; /* 배경 투명 */
                width: 8px; /* 슬림한 너비 */
                margin: 0px 0px 0px 0px;
                border-radius: 4px;
            }

            QScrollBar::handle:vertical {
                background-color: #CBD5E1; /* 기본 은은한 회색 */
                min-height: 30px;
                border-radius: 4px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #94A3B8; /* 조금 더 짙은 회색 */
            }

            QScrollBar::handle:vertical:pressed {
                background-color: #6C5CE7; /* 테마색(보라색) 포인트 */
            }

            QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {
                border: none;
                background: none;
                height: 0px;
            }

            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
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

        # 스크롤바의 최대 범위가 변경될 때(새 버블 추가 시) 자동 스크롤
        self.scroll_area.verticalScrollBar().rangeChanged.connect(
            lambda min_val, max_val: self.scroll_area.verticalScrollBar().setValue(max_val)
        )

        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(10, 10, 10, 10)
        self.chat_layout.setSpacing(12)
        self.chat_layout.addStretch()

        self.scroll_area.setWidget(self.chat_container)
        chat_main_layout.addWidget(self.scroll_area, 1)

        # 첨부 분석 종료 버튼 — 입력창 위에 항상 고정 (숨김 상태로 시작)
        self._attachment_bar = QWidget()
        _bar_layout = QHBoxLayout(self._attachment_bar)
        _bar_layout.setContentsMargins(10, 4, 10, 4)
        self._end_attachment_btn = QPushButton("✕  첨부 분석 종료")
        self._end_attachment_btn.setFixedHeight(32)
        self._end_attachment_btn.setCursor(Qt.PointingHandCursor)
        self._end_attachment_btn.setStyleSheet("""
            QPushButton { background: #FFFFFF; color: #6C5CE7; border: 1px solid #6C5CE7;
                          border-radius: 7px; padding: 0 14px; font-weight: bold; }
            QPushButton:hover { background: #F0EDFE; }
        """)
        self._end_attachment_btn.clicked.connect(self._end_attachment_context)
        _bar_layout.addStretch()
        _bar_layout.addWidget(self._end_attachment_btn)
        _bar_layout.addStretch()
        self._attachment_bar.setVisible(False)
        chat_main_layout.addWidget(self._attachment_bar)

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
        self._attached_file_path = os.path.abspath(os.path.normpath(file_path))
        self._attached_analysis = None
        if self.stacked_layout.currentIndex() == 0:
            self.stacked_layout.setCurrentIndex(1)
        self.add_message(f"📎 [파일 첨부]: {file_path}", is_user=True)
        if self.core is None:
            self.add_message(f"'{file_path}' 파일을 분석할 준비가 되었습니다.", is_user=False)
            return
        self.add_message(f"'{file_path}' 파일을 분석 중입니다...", is_user=False)
        self._file_worker = FolderScanAndTagWorker([file_path], self.core)
        self._file_worker.finished.connect(self._on_file_analyzed)
        self._file_worker.error.connect(self._on_attachment_error)
        self._file_worker.start()

    def _on_file_analyzed(self, summary):
        if self.refresh_manager:
            self.refresh_manager.refresh()
        results = summary.get("results", [])
        self.add_message(self._format_file_analysis_summary(summary), is_user=False)
        if len(results) == 1:
            item = results[0]
            self._attached_file_path = item.get("file_path") or self._attached_file_path
            self._attached_analysis = item.get("result") or {}
            self.add_message(self._format_attached_analysis(self._attached_analysis), is_user=False,
                             kind="result")
            self._add_attachment_context_controls()
        else:
            self._clear_attachment_context()

    @staticmethod
    def _format_file_analysis_summary(summary):
        total = int(summary.get("total", 0) or 0)
        success = int(summary.get("success", 0) or 0)
        failed = len(summary.get("failed", []) or [])
        if total == 1 and success == 1:
            return "파일 분석을 마쳤습니다. 이 파일에 대해 이어서 질문할 수 있습니다."
        if total == 1:
            return "파일을 분석하지 못했습니다. 첨부 분석을 종료하고 일반 검색으로 돌아갑니다."
        if failed:
            return (
                f"폴더 분석을 마쳤습니다. {total}개 파일 중 {success}개를 처리했고 "
                f"{failed}개는 처리하지 못했습니다. 일반 검색으로 돌아갑니다."
            )
        return f"폴더 분석을 마쳤습니다. {success}개 파일을 처리했습니다."

    @staticmethod
    def _format_attached_analysis(result):
        metadata = (result or {}).get("metadata") or {}
        title = metadata.get("display_name") or (result or {}).get("file_info", {}).get("original_name") or "첨부 파일"
        description = metadata.get("description") or metadata.get("summary") or "분석 설명이 없습니다."
        tags = metadata.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        lines = [f"{title}", str(description)]
        if tags:
            lines.append("태그: " + ", ".join(f"#{str(tag).lstrip('#')}" for tag in tags))
        timeline = metadata.get("timeline") or []
        if isinstance(timeline, list) and timeline:
            scenes = []
            for entry in timeline[:5]:
                if isinstance(entry, dict):
                    scenes.append(f"{entry.get('time', '')} {entry.get('scene', '')}".strip())
            if scenes:
                lines.append("주요 장면: " + " / ".join(scenes))
        return "\n".join(lines)

    @staticmethod
    def _should_use_attached_context(query, file_path):
        if not file_path or not query or not os.path.exists(file_path):
            return False
        normalized = " ".join(query.casefold().split())
        explicit_search = ("찾아줘", "검색해", "파일 목록", "무슨 파일", "어떤 파일", "몇 개")
        return not any(marker in normalized for marker in explicit_search)

    def _ask_about_attached_file(self, query):
        extension = os.path.splitext(self._attached_file_path)[1].casefold()
        if extension in {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"}:
            operation = AIFileWorker.ASK_VIDEO
        elif extension in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"}:
            operation = AIFileWorker.ASK_IMAGE
        else:
            operation = AIFileWorker.ASK_DOCUMENT
        if self._ai_services is None:
            analyzer = self.core.analyzer
            self._ai_services = AIServiceContainer(
                main_processor=self.core,
                image_analyzer=analyzer.image_analyzer,
                video_analyzer=analyzer.video_analyzer,
            )
        self.show_loading()
        self._attached_worker = AIFileWorker(
            operation, self._attached_file_path, services=self._ai_services,
            user_prompt=query, parent=self,
        )
        self._attached_worker.succeeded.connect(self._on_attached_answer)
        self._attached_worker.failed.connect(self._on_attachment_error)
        self._attached_worker.start()

    def _on_attached_answer(self, answer):
        self.hide_loading()
        self.add_message(str(answer), is_user=False, kind="result")

    @staticmethod
    def _is_attachment_exit_command(query):
        normalized = " ".join((query or "").casefold().split())
        return normalized in {
            "첨부 분석 종료", "분석 모드 종료", "분석 모드 끝", "첨부 모드 종료",
            "이 파일 그만", "새 대화", "새 대화 시작",
        }

    def _clear_attachment_context(self):
        had_context = bool(self._attached_file_path)
        self._attached_file_path = None
        self._attached_analysis = None
        self._ai_services = None
        self._attachment_bar.setVisible(False)
        return had_context

    def _end_attachment_context(self):
        had_context = self._clear_attachment_context()
        if had_context:
            self.add_message("첨부 파일 분석 모드를 종료했습니다. 이제 일반 검색과 대화를 사용할 수 있습니다.",
                             is_user=False)

    def _on_attachment_error(self, message):
        self.hide_loading()
        self._clear_attachment_context()
        self.add_message(
            f"첨부 파일 분석 중 오류가 발생했습니다: {message}\n"
            "첨부 분석을 종료하고 일반 검색으로 돌아갑니다.",
            is_user=False,
            kind="error",
        )

    def _add_attachment_context_controls(self):
        self._attachment_bar.setVisible(True)

    def process_query(self, query: str):
        self.add_message(query, is_user=True)
        if self.core is not None:
            self._query_worker = QueryProcessWorker(self.core, query, self)
            self._query_worker.finished.connect(self._on_query_result)
            self._query_worker.error.connect(self._on_query_error)
            self._query_worker.start()
            return

        # 코어가 없는 단위 테스트·미리보기 상황에서는 기존 동기 경로를 유지합니다.
        parsed_data = self._query_parser(query)
        if parsed_data.get("status") == "SUCCESS":
            parsed_data = parsed_data["data"]
        try:
            self._display_query_result(self.search_engine.process_query_result(parsed_data))
        except Exception as exc:
            self._on_query_error(str(exc))

    def _on_query_result(self, result):
        self._display_query_result(result)

    def _on_query_error(self, message):
        self.add_message(f"⚠️ 검색 중 오류가 발생했습니다: {message}", is_user=False, kind="error")

    def _display_query_result(self, result):
        action = result.get("action")
        message = result.get("message", "")
        data = result.get("data", [])
        if action == "UPDATE_TABLE":
            self.add_message(message, is_user=False)
            self._render_search_results(data)
        elif action == "SHOW_INVENTORY":
            self.add_message(message, is_user=False)
        elif action == "OPEN_FILE" and data:
            self.add_message(message, is_user=False)
            self.open_in_explorer(data[0][2])
        elif action == "SHOW_CHAT":
            self.add_message(message, is_user=False)
        else:
            self.add_message(f"⚠️ {message or '요청을 처리하지 못했습니다.'}", is_user=False, kind="error")

    def _render_search_results(self, rows):
        MAX_SHOWN = 10
        shown_rows = rows[:MAX_SHOWN]
        if shown_rows:
            self._render_result_cards(shown_rows)

        remaining = len(rows) - MAX_SHOWN
        if remaining > 0:
            self.add_message(f"...외 {remaining}건 더 있습니다.", is_user=False)

    def _render_result_cards(self, rows):
        """검색 결과를 카드 그리드로 렌더링. 카드 클릭 시 탐색기로 파일 위치를 연다."""
        row_layout = QHBoxLayout()

        cards_container = QWidget()
        grid = QGridLayout(cards_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        COLUMNS = 3  # 한 줄에 카드 3개, 넘치면 다음 줄로
        for idx, row in enumerate(rows):
            _id, file_name, file_path, ai_comment, category, *_ = row
            tooltip_lines = []
            if category:
                tooltip_lines.append(f"분류: {category}")
            if file_path:
                tooltip_lines.append(f"경로: {file_path}")
            if ai_comment:
                tooltip_lines.append(f"메모: {ai_comment}")

            card = _FileResultCard(file_name, file_path, tooltip="\n".join(tooltip_lines))
            card.clicked.connect(self._select_and_open_result)

            r, c = divmod(idx, COLUMNS)
            grid.addWidget(card, r, c)

        row_layout.addWidget(cards_container)
        row_layout.addStretch()

        self.chat_layout.insertLayout(self.chat_layout.count() - 1, row_layout)
        self.scroll_to_bottom()

    def _select_and_open_result(self, file_path: str):
        if self.core is not None and hasattr(self.core, "select_search_file"):
            self.core.select_search_file(file_path)
        self.open_in_explorer(file_path)

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
            if w_clean in _EXTENSION_CANDIDATES:
                extensions.append(f".{w_clean}")
            else:
                keywords.append(w)

        return {
            "@TYPE": "@검색",
            "query_keywords": keywords,
            "target_extension": extensions,
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
        # UI 레이아웃 갱신이 완료된 후 스크롤을 최하단으로 이동 (10ms~30ms 후 실행)
        QTimer.singleShot(30, self._do_scroll)

    def _do_scroll(self):
        v_bar = self.scroll_area.verticalScrollBar()
        v_bar.setValue(v_bar.maximum())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            self.on_file_attached(file_path)

        # -----------------------------------------------------------------
    # 로딩(AI 응답 대기) 버블
    # -----------------------------------------------------------------
    def show_loading(self):
        """AI가 검색을 처리하는 동안 대화창에 로딩 버블을 표시한다."""
        if self._loading_widget is not None:
            return  # 이미 표시 중이면 중복 생성 방지

        self._loading_widget = QWidget()
        row_layout = QHBoxLayout(self._loading_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        self._loading_bubble = QLabel("AI가 찾는 중")
        self._loading_bubble.setProperty("class", "aiBubble")
        self._loading_bubble.style().unpolish(self._loading_bubble)
        self._loading_bubble.style().polish(self._loading_bubble)

        row_layout.addWidget(self._loading_bubble)
        row_layout.addStretch()

        # addStretch()로 끝나는 chat_layout의 맨 마지막 자리(스트레치 앞)에 삽입
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._loading_widget)
        self.scroll_to_bottom()

        self._loading_dot_count = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.timeout.connect(self._tick_loading_dots)
        self._loading_timer.start(400)

    def _tick_loading_dots(self):
        if self._loading_bubble is None:
            return
        self._loading_dot_count = (self._loading_dot_count + 1) % 4
        dots = "." * self._loading_dot_count
        self._loading_bubble.setText(f"AI가 찾는 중{dots}")

    def hide_loading(self):
        """로딩 버블 제거"""
        if self._loading_timer is not None:
            self._loading_timer.stop()
            self._loading_timer.deleteLater()
            self._loading_timer = None

        if self._loading_widget is not None:
            self.chat_layout.removeWidget(self._loading_widget)
            self._loading_widget.deleteLater()
            self._loading_widget = None
            self._loading_bubble = None

    def process_query(self, query: str):
        self.add_message(query, is_user=True)
        if self._is_attachment_exit_command(query):
            self._end_attachment_context()
            return
        if self.core is not None and self._should_use_attached_context(query, self._attached_file_path):
            self._ask_about_attached_file(query)
            return
        self.show_loading()

        if self.core is not None:
            self._query_worker = QueryProcessWorker(self.core, query, self)
            self._query_worker.finished.connect(self._on_query_result)
            self._query_worker.error.connect(self._on_query_error)
            self._query_worker.start()
            return

        # 코어가 없는 단위 테스트·미리보기 상황에서는 기존 동기 경로를 유지합니다.
        parsed_data = self._query_parser(query)
        if parsed_data.get("status") == "SUCCESS":
            parsed_data = parsed_data["data"]
        try:
            self._display_query_result(self.search_engine.process_query_result(parsed_data))
        except Exception as exc:
            self._on_query_error(str(exc))
        finally:
            self.hide_loading()

    def _on_query_result(self, result):
        self.hide_loading()
        self._display_query_result(result)

    def _on_query_error(self, message):
        self.hide_loading()
        self.add_message(f"⚠️ 검색 중 오류가 발생했습니다: {message}", is_user=False, kind="error")

        # -----------------------------------------------------------------
    # 검색 결과 파일을 실제 탐색기에서 열기
    # -----------------------------------------------------------------
    def open_in_explorer(self, file_path: str):
        """검색 결과에 저장된 실제 경로를 기준으로 탐색기를 연다.

        파일이면 파일이 들어 있는 폴더를, 폴더면 그 폴더 자체를 연다.
        경로가 비어 있으면 기본 폴더(내 문서 등)를 여는 대신 안내 메시지만 표시한다.
        """
        cleaned = (file_path or "").strip().strip('"')
        if not cleaned:
            self.add_message(
                "⚠️ 검색 결과에 경로 정보가 없어 폴더를 열 수 없습니다.",
                is_user=False, kind="error",
            )
            return

        # DB에 상대 경로가 저장된 경우에도 탐색기가 해석할 수 있는 절대 경로로 바꾼다.
        norm_path = os.path.abspath(os.path.normpath(cleaned))

        if not os.path.exists(norm_path):
            self.add_message(
                f"⚠️ 파일을 찾을 수 없습니다 (이동되었거나 삭제됨): {norm_path}",
                is_user=False, kind="error",
            )
            return

        is_dir = os.path.isdir(norm_path)
        parent_dir = norm_path if is_dir else os.path.dirname(norm_path)

        try:
            if sys.platform.startswith("win"):
                if is_dir:
                    subprocess.Popen(f'explorer "{norm_path}"')
                else:
                    # 경로에 공백이 있으면 리스트 인자 방식은 explorer가 경로를 해석하지 못해
                    # 기본 폴더(내 문서)를 열어버린다. 따옴표로 감싼 명령 문자열로 전달한다.
                    subprocess.Popen(f'explorer /select,"{norm_path}"')
            elif sys.platform == "darwin":
                subprocess.Popen(["open", norm_path] if is_dir else ["open", "-R", norm_path])
            else:
                # Linux 등: 파일 자체 선택 기능이 없어 상위 폴더만 연다
                subprocess.Popen(["xdg-open", parent_dir])
        except Exception as e:
            self.add_message(
                f"⚠️ 탐색기를 여는 중 오류가 발생했습니다: {e}",
                is_user=False, kind="error",
            )
