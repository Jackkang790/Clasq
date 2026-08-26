from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QPushButton, QVBoxLayout


class Sidebar(QFrame):
    # 페이지 이동 시그널 (1: 검색, 2: 정리, 3: 저장목록)
    # 0(설정)은 더 이상 사이드바가 아니라 TitleBar의 톱니바퀴 드롭다운이 담당한다.
    page_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(180)

        self.setStyleSheet("""
            Sidebar {
                background-color: #F8F9FA;
                border-right: 1px solid #E5E5E5;
            }

            /* 사이드바 내 기본 메뉴 버튼 재정의 */
            Sidebar QPushButton {
                text-align: center;
                padding: 10px 12px;
                background-color: transparent;
                color: #333333;
                font-weight: normal;
            }
            Sidebar QPushButton:hover {
                background-color: #E9ECEF;
            }
        """)

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # 사이드 바 버튼
        self.btn_search = QPushButton("검색하기")
        self.btn_organize = QPushButton("정리하기")
        self.btn_saved = QPushButton("저장목록")

        for btn in (self.btn_search, self.btn_organize, self.btn_saved):
            btn.setFocusPolicy(Qt.NoFocus)

        # 레이아웃 배치
        layout.addWidget(self.btn_search)
        layout.addWidget(self.btn_organize)
        layout.addWidget(self.btn_saved)

        # 시그널 연결
        self.btn_search.clicked.connect(lambda: self.page_changed.emit(1))
        self.btn_organize.clicked.connect(lambda: self.page_changed.emit(2))
        self.btn_saved.clicked.connect(lambda: self.page_changed.emit(3))

    def set_active(self, index: int):
        """현재 활성 탭 버튼을 강조 표시한다. index: 1=검색, 2=정리, 3=저장목록."""
        mapping = {1: self.btn_search, 2: self.btn_organize, 3: self.btn_saved}
        for idx, btn in mapping.items():
            if idx == index:
                btn.setStyleSheet(
                    "background-color: #6C5CE7; color: white; "
                    "font-weight: bold; border-radius: 8px; padding: 10px 12px;"
                )
            else:
                btn.setStyleSheet("")