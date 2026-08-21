from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QWidget,
)


class FileUploadView(QWidget):
    # 외부로 내보낼 신호(Signal) 정의
    message_submitted = Signal(str)
    file_attached = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # 1. 파일 첨부 (+) 버튼
        self.plus_btn = QPushButton("+")
        self.plus_btn.setFixedSize(36, 36)
        self.plus_btn.setStyleSheet("""
            QPushButton {
                border: 1.5px solid #CED4DA;
                border-radius: 18px;
                font-size: 18px;
                background-color: #FFFFFF;
                color: #212529;
            }
            QPushButton:hover {
                background-color: #F8F9FA;
            }
        """)

        # 드롭다운 메뉴 (HTML select 역할)
        self.attach_menu = QMenu(self)
        action_file = self.attach_menu.addAction("📄 파일 선택")
        action_clipboard = self.attach_menu.addAction("📋 클립보드 파일 붙여넣기")

        action_file.triggered.connect(self.open_file_dialog)
        action_clipboard.triggered.connect(self.paste_from_clipboard)
        self.plus_btn.clicked.connect(self.show_select_menu)

        # 2. 텍스트 입력창
        self.input_field = QLineEdit()
        self.input_field.setProperty("class", "searchInput")
        self.input_field.setFixedHeight(42)
        self.input_field.setPlaceholderText("무엇이든 물어보세요 (파일 드래그 가능)")
        self.input_field.returnPressed.connect(self.submit_message)
        self.input_field.installEventFilter(self)

        # 3. 전송 버튼
        self.send_btn = QPushButton("↑")
        self.send_btn.setProperty("class", "sendBtn")
        self.send_btn.setFixedSize(36, 36)
        self.send_btn.clicked.connect(self.submit_message)

        layout.addWidget(self.plus_btn)
        layout.addWidget(self.input_field, 1)
        layout.addWidget(self.send_btn)

    def show_select_menu(self):
        btn_pos = self.plus_btn.mapToGlobal(self.plus_btn.rect().topLeft())
        menu_pos = btn_pos - QPoint(0, self.attach_menu.sizeHint().height() + 5)
        self.attach_menu.exec_(menu_pos)

    def open_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "파일 선택", "", "All Files (*.*)")
        if file_path:
            self.file_attached.emit(file_path)

    def paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        if mime_data.hasUrls():
            for url in mime_data.urls():
                self.file_attached.emit(url.toLocalFile())

    def eventFilter(self, obj, event):
        if (
            obj == self.input_field
            and event.type() == QEvent.KeyPress
            and event.modifiers() == Qt.ControlModifier
            and event.key() == Qt.Key_V
        ):
            clipboard = QApplication.clipboard()
            if clipboard.mimeData().hasUrls():
                self.paste_from_clipboard()
                return True
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            self.file_attached.emit(url.toLocalFile())

    def submit_message(self):
        text = self.input_field.text().strip()
        if text:
            self.message_submitted.emit(text)
            self.input_field.clear()