import os
import sys
import logging
import ctypes

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QEventLoop, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
    QMessageBox,
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
from src.ui.refresh_manager import RefreshManager
from src.ui.widgets.progress_dialog import TaskProgressDialog
from src.utils.workers import OllamaInitWorker
from src.ai.config import get_ai_mode
from src.utils.app_paths import assets_dir, database_path
from src.utils.logging_setup import initialize_runtime_logging, shutdown_runtime_logging

logger = logging.getLogger(__name__)

# Inno Setup uses the same per-session mutex to refuse install/uninstall while
# Clasq still owns packaged files.  The Windows kernel releases it on process
# exit, including abnormal exit, so no filesystem lock or stale cleanup exists.
INSTALLER_APP_MUTEX = "Clasq-21E38F55-7A79-49A4-84E6-1F6E41F922E2"
_installer_mutex_handle = None


def _acquire_installer_app_mutex():
    global _installer_mutex_handle
    if not sys.platform.startswith("win") or _installer_mutex_handle is not None:
        return _installer_mutex_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, INSTALLER_APP_MUTEX)
    if not handle:
        raise OSError(ctypes.get_last_error(), "failed to create installer app mutex")
    _installer_mutex_handle = handle
    return handle

# 모듈 로드 시 1회만 읽어 캐싱 (환경변수가 실행 중 바뀌어도 앱 재시작 필요)
_AI_MODE = get_ai_mode()

# 스택 위젯 인덱스 (Sidebar.page_changed, TitleBar 설정 드롭다운 둘 다 이 순서를 따름)
IDX_SETTINGS = 0
IDX_SEARCH = 1
IDX_ORGANIZE = 2
IDX_SAVED = 3


# ---------------------------------------------------------------------------
# AI 상태 배너 (llama_server / remote 모드에서만 사용)
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
        self.setFixedHeight(28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(6)
        self._label = QLabel("AI 환경 확인 중...")
        self._label.setStyleSheet("color:#495057; font-size:12px;")
        layout.addWidget(self._label)
        layout.addStretch()
        self.setStyleSheet(self._STYLE_PENDING)

    def set_ready(self):
        self._label.setText("AI 준비 완료")
        self.setStyleSheet(self._STYLE_READY)
        QTimer.singleShot(3000, self.hide)

    def set_error(self, message: str):
        short = message[:80] + "..." if len(message) > 80 else message
        self._label.setText(f"AI 서버 실행 실패: {short}")
        self.setStyleSheet(self._STYLE_ERROR)


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self, server_manager=None, ai_startup_error=None):
        super().__init__()
        self.setWindowTitle("AI 파일 관리 시스템")
        self.resize(1100, 700)

        # llama-server 인스턴스 참조 (앱 종료 시 shutdown)
        self._server_manager = server_manager
        self._app_shutting_down = False
        self._ai_banner = None

        # 코어 시스템 초기화 (Ollama/Qwen 모두 ClasqCore 공통 사용)
        self.core = ClasqCore(
            db_path=database_path(),
            text_model=OllamaManager.MODEL_NAME,
        )
        self.refresh_manager = RefreshManager(self)

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

        # ---- AI 상태 배너 (llama_server / remote 모드에서만 표시) ----
        if _AI_MODE != "ollama":
            self._ai_banner = _AIStatusBanner()
            outer_layout.addWidget(self._ai_banner)

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
        self.settings_view = SettingsView(
            self.stacked_widget,
            core=self.core,
            refresh_manager=self.refresh_manager,
            server_manager=self._server_manager,
        )
        self.search_view = SearchView(core=self.core, refresh_manager=self.refresh_manager)
        self.organize_view = OrganizeView(core=self.core, refresh_manager=self.refresh_manager)
        self.saved_view = SavedView(core=self.core, refresh_manager=self.refresh_manager)
        self.stacked_widget.addWidget(self.settings_view)
        self.stacked_widget.addWidget(self.search_view)
        self.stacked_widget.addWidget(self.organize_view)
        self.stacked_widget.addWidget(self.saved_view)
        self.refresh_manager.database_changed.connect(self._refresh_data_models)

        content_layout.addWidget(self.sidebar)
        content_layout.addWidget(self.stacked_widget)
        outer_layout.addWidget(content_container, stretch=1)

        # ---- 뒤로/앞으로 내비게이션 히스토리 ----
        self._history = [IDX_SEARCH]
        self._history_pos = 0
        self.stacked_widget.setCurrentIndex(IDX_SEARCH)  # 기본 시작 화면 - 검색하기
        self.sidebar.set_active(IDX_SEARCH)
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

        # ---- AI 배너 최종 상태 설정 ----
        if self._ai_banner is not None:
            if ai_startup_error:
                self._ai_banner.set_error(ai_startup_error)
            else:
                self._ai_banner.set_ready()

    # -----------------------------------------------------------------
    # 앱 종료 시 llama-server 프로세스 정리
    # -----------------------------------------------------------------
    def closeEvent(self, event):
        logger.info("application shutdown requested")
        self._app_shutting_down = True
        if self._server_manager is not None:
            self._server_manager.shutdown()
        logger.info("application window closed server_cleanup=%s", self._server_manager is not None)
        event.accept()

    # -----------------------------------------------------------------
    # 내비게이션 (뒤로가기 / 앞으로가기 / 사이드바·설정 이동)
    # -----------------------------------------------------------------
    def _navigate(self, index, record=True):
        self.stacked_widget.setCurrentIndex(index)
        self.sidebar.set_active(index)
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

    def _refresh_data_models(self):
        """모든 DB 쓰기 후 각 화면이 같은 최신 모델을 보도록 동기화합니다."""
        self.core.sync_db_with_disk()
        self.organize_view._load_files_from_db()
        self.saved_view.load_data()

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


# ---------------------------------------------------------------------------
# AI 시작 함수들
# ---------------------------------------------------------------------------

def load_ollama_with_progress():
    """Ollama 모델 로딩을 worker 스레드에서 수행하며 Progress Dialog로 진행 단계를 보여줍니다."""
    dialog = TaskProgressDialog(
        "Ollama 모델 로딩 중", "AI 모델을 준비하고 있습니다...", unit="단계",
    )
    worker = OllamaInitWorker()
    loop = QEventLoop()
    result = {"success": False, "message": "Ollama를 초기화하지 못했습니다.", "done": False}

    def on_progress(current, total, status):
        dialog.update_progress(current, total, status=status)

    def on_completed(success, message):
        result["success"] = success
        result["done"] = True
        if message:
            result["message"] = message
        dialog.close()
        loop.quit()

    worker.progress.connect(on_progress)
    worker.completed.connect(on_completed)
    worker.start()
    dialog.show()
    if not result["done"]:
        loop.exec()
    worker.wait()
    return result["success"], result["message"]


def load_llama_with_progress():
    """StartupWorker 를 사용한 llama-server 환경 초기화.

    HW 감지 → 프로필 선택 → 저장공간 확인 → 모델 준비 → 서버 시작
    단계별 진행을 TaskProgressDialog 로 표시한다.
    Ollama 로의 자동 fallback 없음: 실패 시 명확한 오류 상태를 반환한다.
    """
    from src.ai.startup_worker import StartupWorker

    dialog = TaskProgressDialog(
        "Clasq AI 환경 준비 중",
        "AI 실행 환경을 확인하고 있습니다...",
        unit="단계",
        cancellable=True,
    )
    worker = StartupWorker()
    loop = QEventLoop()
    result = {
        "success": False,
        "message": "AI 초기화 실패",
        "done": False,
        "server_manager": None,
    }

    def on_phase(_phase, label):
        # 단계 진행은 indeterminate 바로 표시 (total=0)
        dialog.update_progress(0, 0, status=label)

    def on_download_progress(filename, received, total):
        safe_received = max(0, min(received, total)) if total else max(0, received)
        recv_gb = safe_received / 1024 ** 3
        tot_gb  = total   / 1024 ** 3
        pct     = int(safe_received / total * 100) if total else 0
        dialog.update_progress(
            safe_received, total,
            status=f"모델 다운로드 중: {filename}",
            detail=f"{pct}%  {recv_gb:.1f}GB / {tot_gb:.1f}GB",
        )

    def on_ready(success, message):
        result["success"] = success
        result["done"] = True
        result["message"] = message
        if success:
            result["server_manager"] = worker.server_manager
        dialog.close()
        loop.quit()

    worker.phase_changed.connect(on_phase)
    worker.progress_changed.connect(on_download_progress)
    worker.ready.connect(on_ready)
    dialog.canceled.connect(worker.cancel)
    worker.start()
    dialog.show()
    if not result["done"]:
        loop.exec()
    worker.wait()
    return result["success"], result["message"], result["server_manager"]


def confirm_first_run_model_download(parent=None):
    """Ask before provisioning the large local model on an empty cache."""
    from src.ai.model_downloader import ModelDownloader
    from src.ai.runtime_profile import PROFILE_QWEN3VL_8B_Q4KM_CUDA
    from src.utils.app_paths import models_dir

    state = ModelDownloader(
        PROFILE_QWEN3VL_8B_Q4KM_CUDA,
        models_dir=models_dir(),
    ).cache_state()
    if state and all(value == "valid" for value in state.values()):
        return True
    message = (
        "로컬 AI 기능을 사용하려면 약 6.2GB의 모델 파일을 다운로드해야 합니다.\n\n"
        "인터넷 연결이 필요하며, 다운로드 완료 후 모델은 이 PC에서 로컬로 사용됩니다.\n"
        "지금 다운로드하시겠습니까?"
    )
    return QMessageBox.question(
        parent,
        "로컬 AI 모델 준비",
        message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) == QMessageBox.Yes


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def main():
    _acquire_installer_app_mutex()
    log_path = initialize_runtime_logging()
    logger.info(
        "application startup mode=%s platform=%s file_logging=%s",
        "packaged" if getattr(sys, "frozen", False) else "source",
        sys.platform,
        log_path is not None,
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    server_manager = None
    ai_startup_error = None

    if _AI_MODE == "ollama":
        # ── 기존 Ollama 시작 경로 (코드 변경 없음) ──────────────────────────
        succeeded, error_message = load_ollama_with_progress()
        if not succeeded:
            QMessageBox.critical(
                None, "Ollama 연결 실패",
                f"{error_message}\nOllama 설치·실행 상태와 모델을 확인한 뒤 다시 시도해주세요.",
            )
            return 1

    else:
        # ── llama_server / remote: StartupWorker 경로 ────────────────────────
        # Ollama(gemma3/llava)는 호출하지 않는다.
        if confirm_first_run_model_download():
            succeeded, error_message, server_manager = load_llama_with_progress()
        else:
            succeeded = False
            error_message = "모델 다운로드는 나중에 진행할 수 있습니다."
        if server_manager is not None:
            from src.ai.qwen_client import set_runtime_recovery
            set_runtime_recovery(server_manager.recover_if_needed)
        if not succeeded:
            # AI 실패 → 오류를 표시하되 앱은 계속 실행 (검색·DB 기능 사용 가능)
            # Ollama 자동 전환 없음.
            QMessageBox.warning(
                None, "AI 서버 시작 실패",
                f"{error_message}\n\nAI 기능 없이 계속 실행합니다.\n(검색·파일 관리 기능은 사용 가능)",
            )
            ai_startup_error = error_message

    qss_path = os.path.join(assets_dir(), "styles", "light.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    window = MainWindow(server_manager=server_manager, ai_startup_error=ai_startup_error)
    window.show()

    try:
        return app.exec()
    except Exception:
        logger.exception("unexpected application event-loop failure")
        raise
    finally:
        logger.info("application exiting")
        shutdown_runtime_logging()


if __name__ == "__main__":
    sys.exit(main())
