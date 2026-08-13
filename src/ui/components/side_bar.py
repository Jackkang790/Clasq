from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout


class Sidebar(QFrame):
    # 페이지 이동 시그널 (0: 설정, 1: 검색, 2: 정리, 3: 저장목록)
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
                text-align: left;
                padding: 10px 12px;
                background-color: transparent;
                color: #333333;
                font-weight: normal;
            }
            Sidebar QPushButton:hover {
                background-color: #E9ECEF;
            }
            
            /* 톱니바퀴 (설정) 버튼 전용 예외 스타일 */
            QPushButton#btnSettings {
                background-color: transparent;
                color: #555555;
                font-size: 18px;
                border: none;
                border-radius: 6px;
                padding: 0px;
            }
            QPushButton#btnSettings:hover {
                background-color: #E2E6EA;
                color: #000000;
            }
        """)

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # 톱니바퀴 (설정) 버튼 영역 (상단 좌측 정렬)
        settings_layout = QHBoxLayout()
        settings_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setFixedSize(36, 36)
        self.btn_settings.setFocusPolicy(Qt.NoFocus)
        self.btn_settings.setObjectName("btnSettings")

        settings_layout.addWidget(self.btn_settings)
        settings_layout.addStretch()

        # 사이드 바 버튼
        self.btn_search = QPushButton("🔍  검색하기")
        self.btn_organize = QPushButton("📁  정리하기")
        self.btn_saved = QPushButton("💾  저장목록")

        for btn in (self.btn_search, self.btn_organize, self.btn_saved):
            btn.setFocusPolicy(Qt.NoFocus)

        # 레이아웃 배치
        layout.addLayout(settings_layout)
        layout.addSpacing(10)  # 톱니바퀴와 메뉴 사이 간격
        layout.addWidget(self.btn_search)
        layout.addWidget(self.btn_organize)
        layout.addWidget(self.btn_saved)

        # 시그널 연결
        self.btn_settings.clicked.connect(lambda: self.page_changed.emit(0))
        self.btn_search.clicked.connect(lambda: self.page_changed.emit(1))
        self.btn_organize.clicked.connect(lambda: self.page_changed.emit(2))
        self.btn_saved.clicked.connect(lambda: self.page_changed.emit(3))