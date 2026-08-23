import logging
import os
import sys

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

# frozen(PyInstaller EXE) / 개발 환경 양쪽에서 올바른 루트 경로 설정
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)

from src.ai.startup_worker import StartupWorker, StartupPhase
from src.ui.views.organize_view import OrganizeView
from src.ui.views.saved_view import SavedView
from src.ui.views.search_view import SearchView
from src.ui.views.settings_view import SettingsView
from src.ui.components.side_bar import Sidebar
from src.ui.components.title_bar import TitleBar

# 스택 위젯 인덱스 (Sidebar.page_changed, TitleBar 설정 드롭다운 둘 다 이 순서를 따름)
IDX_SETTINGS = 0
IDX_SEARCH   = 1
IDX_ORGANIZE = 2
IDX_SAVED    = 3

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AI 상태 배너 (타이틀바 아래, 기존 뷰 무변경)
# ---------------------------------------------------------------------------

class _AIStatusBanner(QWidget):
    """앱 시작 시 AI 환경 준비 상태를 표시하는 슬림 배너.

    준비 완료 3초 후 자동으로 숨겨진다.
    오류 발생 시 오류 색상으로 유지된다.
    """

    _STYLE_PENDING = "background-color:#FFF3CD; border-bottom:1px solid #FFDDA1;"
    _STYLE_READY   = "background-color:#D4EDDA; border-bottom:1px solid #C3E6CB;"
    _STYLE_ERROR   = "background-color:#F8D7DA; border-bottom:1px solid #F5C6CB;"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        self._label    = QLabel("AI 환경 확인 중...")
        self._progress = QLabel("")
        self._label.setStyleSheet("color:#495057; font-size:12px;")
        self._progress.setStyleSheet("color:#6C757D; font-size:11px;")

        layout.addWidget(self._label)
        layout.addWidget(self._progress)
        layout.addStretch()

        self.setStyleSheet(self._STYLE_PENDING)

    # ── public ──────────────────────────────────────────────────────────

    def set_phase(self, label: str) -> None:
        self._label.setText(label)
        self.setStyleSheet(self._STYLE_PENDING)

    def set_download_progress(self, received: int, total: int) -> None:
        if total <= 0:
            return
        pct      = int(received / total * 100)
        recv_gb  = received / 1024 ** 3
        total_gb = total    / 1024 ** 3
        self._progress.setText(f"{pct}%  {recv_gb:.1f}GB / {total_gb:.1f}GB")

    def set_ready(self) -> None:
        self.setStyleSheet(self._STYLE_READY)
        self._label.setStyleSheet("color:#155724; font-size:12px;")
        self._label.setText("AI 준비 완료")
        self._progress.setText("")
        QTimer.singleShot(3000, self.hide)

    def set_error(self, msg: str) -> None:
        self.setStyleSheet(self._STYLE_ERROR)
        self._label.setStyleSheet("color:#721C24; font-size:12px;")
        self._label.setText(f"AI 사용 불가: {msg}")
        self._progress.setText("")


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self._server_manager  = None   # StartupWorker 완료 후 설정됨
        self._startup_worker  = None

        self.setWindowTitle("AI 파일 관리 시스템")
        self.resize(1100, 700)

        # 프레임리스 창
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setStyleSheet("QMainWindow { border: 1px solid #E4E6EF; }")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        outer_layout = QVBoxLayout(central_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # 상단바
        self.title_bar = TitleBar()
        outer_layout.addWidget(self.title_bar)

        # AI 상태 배너 (타이틀바 바로 아래)
        self._ai_banner = _AIStatusBanner()
        outer_layout.addWidget(self._ai_banner)

        # 사이드바 + 스택 위젯
        content_container = QWidget()
        content_layout = QHBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.sidebar        = Sidebar()
        self.stacked_widget = QStackedWidget()

        self.stacked_widget.addWidget(SettingsView(self.stacked_widget))
        self.stacked_widget.addWidget(SearchView())
        self.stacked_widget.addWidget(OrganizeView())
        self.stacked_widget.addWidget(SavedView())

        content_layout.addWidget(self.sidebar)
        content_layout.addWidget(self.stacked_widget)
        outer_layout.addWidget(content_container, stretch=1)

        # 내비게이션 히스토리
        self._history     = [IDX_SEARCH]
        self._history_pos = 0
        self.stacked_widget.setCurrentIndex(IDX_SEARCH)
        self._sync_nav_buttons()

        # 시그널 연결
        self.sidebar.page_changed.connect(self._navigate)
        self.title_bar.backClicked.connect(self._go_back)
        self.title_bar.forwardClicked.connect(self._go_forward)
        self.title_bar.settingsSelected.connect(lambda: self._navigate(IDX_SETTINGS))
        self.title_bar.minimizeClicked.connect(self._animated_minimize)
        self.title_bar.maximizeClicked.connect(self._toggle_pseudo_maximize)
        self.title_bar.closeClicked.connect(self.close)

        # 애니메이션 상태값
        self._is_pseudo_maximized = False
        self._normal_geometry     = None
        self._geo_anim            = None
        self._opacity_anim        = None

        # AI 환경 초기화 (백그라운드)
        self._start_ai_setup()

    # ── AI 초기화 ─────────────────────────────────────────────────────────

    def _start_ai_setup(self) -> None:
        worker = StartupWorker(parent=self)
        worker.phase_changed.connect(self._on_startup_phase)
        worker.progress_changed.connect(self._on_startup_progress)
        worker.ready.connect(self._on_startup_ready)
        self._startup_worker = worker
        worker.start()

    def _on_startup_phase(self, phase: StartupPhase, label: str) -> None:
        self._ai_banner.set_phase(label)

    def _on_startup_progress(self, filename: str, received: int, total: int) -> None:
        self._ai_banner.set_download_progress(received, total)

    def _on_startup_ready(self, success: bool, error_msg: str) -> None:
        if success:
            self._server_manager = self._startup_worker.server_manager
            self._ai_banner.set_ready()
            log.info("AI startup complete")
        else:
            self._ai_banner.set_error(error_msg)
            log.warning("AI startup failed: %s", error_msg)
            # 앱은 계속 실행. AI 기능 사용 시 연결 오류가 자연스럽게 전달됨.

    # ── 종료 처리 ─────────────────────────────────────────────────────────

    def closeEvent(self, event):
        # 백그라운드 워커 종료
        if self._startup_worker is not None and self._startup_worker.isRunning():
            self._startup_worker.terminate()
            self._startup_worker.wait(3000)

        # 이 앱이 직접 시작한 llama-server 프로세스만 종료
        if self._server_manager is not None:
            self._server_manager.shutdown()

        super().closeEvent(event)

    # ── 내비게이션 ────────────────────────────────────────────────────────

    def _navigate(self, index, record=True):
        self.stacked_widget.setCurrentIndex(index)
        if record:
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
        self.title_bar.set_nav_enabled(
            self._history_pos > 0,
            self._history_pos < len(self._history) - 1,
        )

    # ── 최소화 애니메이션 ─────────────────────────────────────────────────

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
        self.setWindowOpacity(1.0)

    # ── 최대화/복원 애니메이션 ────────────────────────────────────────────

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


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

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
