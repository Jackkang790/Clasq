import os
import sys

from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.ui.views.organize_view import OrganizeView
from src.ui.views.saved_view import SavedView
from src.ui.views.search_view import SearchView
from src.ui.views.settings_view import SettingsView
from src.ui.components.side_bar import Sidebar


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 파일 관리 시스템")
        self.resize(1100, 700)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        content_layout = QHBoxLayout(central_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.stacked_widget = QStackedWidget()

        # 인덱스 순서대로 스택에 추가
        # Index 0: 설정
        # Index 1: 검색
        # Index 2: 정리
        # Index 3: 저장목록
        self.stacked_widget.addWidget(
        SettingsView(self.stacked_widget)
        )
        self.stacked_widget.addWidget(SearchView())
        self.stacked_widget.addWidget(OrganizeView())
        self.stacked_widget.addWidget(SavedView())

        # 기본 시작 화면 지정 1- 검색하기
        self.stacked_widget.setCurrentIndex(1)

        content_layout.addWidget(self.sidebar)
        content_layout.addWidget(self.stacked_widget)

        # 사이드바 버튼 클릭 -> 스택 위젯 페이지 변경 연동
        self.sidebar.page_changed.connect(self.stacked_widget.setCurrentIndex)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    qss_path = os.path.join(BASE_DIR, "assets", "styles", "light.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()