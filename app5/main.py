import os
import sys

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QVBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.utils.core import ClasqCore
from ollama_manager import OllamaManager
from src.ui.views.organize_view import OrganizeView
from src.ui.views.saved_view import SavedView
from src.ui.views.search_view import SearchView
from src.ui.views.settings_view import SettingsView
from src.ui.components.side_bar import Sidebar
from src.ui.components.title_bar import TitleBar

# 스택 위젯 인덱스 (Sidebar.page_changed, TitleBar 설정 드롭다운 둘 다 이 순서를 따름)
IDX_SETTINGS = 0
IDX_SEARCH = 1
IDX_ORGANIZE = 2
IDX_SAVED = 3


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 파일 관리 시스템")
        self.resize(1100, 700)

        # 코어 시스템 초기화
        self.core = ClasqCore(
            db_path=os.path.join(BASE_DIR, "file_manager.db"),
            text_model=OllamaManager.MODEL_NAME,
        )

        # ---- 프레임리스 창: 상단바를 직접 그릴 것이므로 OS 기본 타이틀바를 없앤다 ----
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        # 프레임리스 창은 OS 기본 리사이즈 핸들/그림자가 사라지므로,
        # central widget에 얇은 테두리만 둬서 창 경계를 시각적으로 표시한다.
        self.setStyleSheet("QMainWindow { border: 1px solid #E4E6EF; }")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        outer_layout = QVBoxLayout(central_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # ---- 상단바 ----
        self.title_bar = TitleBar()
        outer_layout.addWidget(self.title_bar)

        # ---- 사이드바 + 스택 위젯 (기존 구조 그대로) ----
        content_container = QWidget()
        content_layout = QHBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.stacked_widget = QStackedWidget()

        # 인덱스 순서대로 스택에 추가
        # Index 0: 설정
        # Index 1: 검색
        # Index 2: 정리
        # Index 3: 저장목록
        self.stacked_widget.addWidget(SettingsView(self.stacked_widget, core=self.core))
        self.stacked_widget.addWidget(SearchView(core=self.core))
        self.stacked_widget.addWidget(OrganizeView(core=self.core))
        self.stacked_widget.addWidget(SavedView(core=self.core))

        content_layout.addWidget(self.sidebar)
        content_layout.addWidget(self.stacked_widget)
        outer_layout.addWidget(content_container, stretch=1)

        # ---- 뒤로/앞으로 내비게이션 히스토리 ----
        self._history = [IDX_SEARCH]
        self._history_pos = 0
        self.stacked_widget.setCurrentIndex(IDX_SEARCH)  # 기본 시작 화면 - 검색하기
        self._sync_nav_buttons()

        # 사이드바 버튼 클릭 -> 히스토리에 기록하며 페이지 이동
        self.sidebar.page_changed.connect(self._navigate)

        # 상단바 시그널 연결
        self.title_bar.backClicked.connect(self._go_back)
        self.title_bar.forwardClicked.connect(self._go_forward)
        self.title_bar.settingsSelected.connect(lambda: self._navigate(IDX_SETTINGS))
        self.title_bar.minimizeClicked.connect(self._animated_minimize)
        self.title_bar.maximizeClicked.connect(self._toggle_pseudo_maximize)
        self.title_bar.closeClicked.connect(self.close)

        # ---- 애니메이션 상태값 ----
        self._is_pseudo_maximized = False
        self._normal_geometry = None
        self._geo_anim = None
        self._opacity_anim = None

    # -----------------------------------------------------------------
    # 내비게이션 (뒤로가기 / 앞으로가기 / 사이드바·설정 이동)
    # -----------------------------------------------------------------
    def _navigate(self, index, record=True):
        self.stacked_widget.setCurrentIndex(index)
        if record:
            # 새 경로로 이동하면 현재 위치 이후의 forward 히스토리는 버린다
            self._history = self._history[: self._history_pos + 1]
            if not self._history or self._history[-1] != index:
                self._history.append(index)
                self._history_pos = len(self._history) - 1
        self._sync_nav_buttons()

    def _go_back(self):
        if self._history_pos > 0:
            self._history_pos -= 1
            self._navigate(self._history[self._history_pos], record=False)

    def _go_forward(self):
        if self._history_pos < len(self._history) - 1:
            self._history_pos += 1
            self._navigate(self._history[self._history_pos], record=False)

    def _sync_nav_buttons(self):
        can_back = self._history_pos > 0
        can_forward = self._history_pos < len(self._history) - 1
        self.title_bar.set_nav_enabled(can_back, can_forward)

    # -----------------------------------------------------------------
    # 최소화 애니메이션: 창을 서서히 투명하게 만든 뒤 실제로 최소화한다.
    # -----------------------------------------------------------------
    def _animated_minimize(self):
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(180)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(self._do_minimize)
        self._opacity_anim = anim
        anim.start()

    def _do_minimize(self):
        self.showMinimized()
        self.setWindowOpacity(1.0)  # 다시 나타날 때는 즉시 원래 상태로

    # -----------------------------------------------------------------
    # 최대화/복원 애니메이션: 실제 OS 최대화 대신, 창 geometry를 부드럽게
    # 화면 전체 크기로/원래 크기로 애니메이션한다 (프레임리스 창이라 가능).
    # -----------------------------------------------------------------
    def _toggle_pseudo_maximize(self):
        screen_geo = self.screen().availableGeometry()

        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(self.geometry())

        if self._is_pseudo_maximized:
            anim.setEndValue(self._normal_geometry or screen_geo)
            self._is_pseudo_maximized = False
        else:
            self._normal_geometry = self.geometry()
            anim.setEndValue(screen_geo)
            self._is_pseudo_maximized = True

        self.title_bar.set_maximized_icon(self._is_pseudo_maximized)
        self._geo_anim = anim
        anim.start()


def main():
    print("AI 실행 환경을 준비하고 있습니다...")
    if not OllamaManager.initialize():
        print("Ollama 초기화에 실패해 프로그램을 시작할 수 없습니다.")
        return 1

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    qss_path = os.path.join(BASE_DIR, "assets", "styles", "light.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
