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


class SearchView(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
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
        # 파일이 들어왔을 때 화면 전환 및 메시지 출력
        if self.stacked_layout.currentIndex() == 0:
            self.stacked_layout.setCurrentIndex(1)

        self.add_message(f"📎 [파일 첨부]: {file_path}", is_user=True)
        self.add_message(f"'{file_path}' 파일을 분석 중입니다...", is_user=False)

    def process_query(self, query: str):
        self.add_message(query, is_user=True)
        ai_response = f"'{query}'에 대한 검색 결과를 확인했습니다."
        self.add_message(ai_response, is_user=False)

    def add_message(self, text: str, is_user: bool = True):
        row_layout = QHBoxLayout()
        bubble = QLabel(text)
        bubble.setWordWrap(True)

        if is_user:
            bubble.setProperty("class", "userBubble")
            row_layout.addStretch()
            row_layout.addWidget(bubble)
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