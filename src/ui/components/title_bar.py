"""
src/ui/components/title_bar.py

프레임리스 창(Qt.FramelessWindowHint)에서 쓰는 커스텀 상단바.
SVG 아이콘 파일(assets/styles/icons)을 직접 로드하도록 수정된 버전.
"""
import os
from PySide6.QtCore import Qt, Signal, Property, QSize, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QWidget, QPushButton, QHBoxLayout, QLabel, QFrame
from src.utils.app_paths import assets_dir

# 경로 설정 (src/ui/components/title_bar.py 기준)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", ".."))
ICON_DIR = os.path.join(assets_dir(), "styles", "icons")

PRIMARY = "#6C5CE7"
BORDER = "#E4E6EF"
TEXT_MAIN = "#2D2D3A"
TEXT_SUB = "#8A8CA5"
HOVER_BG = "#F0F0F7"


class _AnimatedIconButton(QPushButton):
    """hover 시 배경색이 전환되는 QIcon 기반 아이콘 버튼"""

    def __init__(self, icon_filename, hover_color=HOVER_BG, parent=None):
        super().__init__("", parent)
        self._normal_color = QColor("#FFFFFF")
        self._hover_color = QColor(hover_color)
        self._bg_color = QColor(self._normal_color)

        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(36, 30)
        self.setIconSize(QSize(18, 18))
        self.setFlat(True)

        if icon_filename:
            self.set_icon_file(icon_filename)

        self._anim = QPropertyAnimation(self, b"bgColor", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self._update_style()

    def set_icon_file(self, filename):
        path = os.path.join(ICON_DIR, filename)
        if os.path.exists(path):
            self.setIcon(QIcon(path))

    def getBgColor(self):
        return self._bg_color

    def setBgColor(self, color):
        self._bg_color = color
        self._update_style()

    bgColor = Property(QColor, getBgColor, setBgColor)

    def _update_style(self):
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {self._bg_color.name()};
                border: none;
                border-radius: 6px;
            }}
            QPushButton:disabled {{
                background-color: transparent;
                opacity: 0.3;
            }}
        """)

    def _animate_to(self, color):
        self._anim.stop()
        self._anim.setStartValue(self._bg_color)
        self._anim.setEndValue(color)
        self._anim.start()

    def enterEvent(self, event):
        if self.isEnabled():
            self._animate_to(self._hover_color)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_to(self._normal_color)
        super().leaveEvent(event)


class TitleBar(QWidget):
    """커스텀 상단바"""

    backClicked = Signal()
    forwardClicked = Signal()
    minimizeClicked = Signal()
    maximizeClicked = Signal()
    closeClicked = Signal()
    settingsSelected = Signal()

    def __init__(self, title="Clasq", parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(44)
        self.setStyleSheet(f"""
            QWidget#titleBar {{ background-color: #FFFFFF; border-bottom: 1px solid {BORDER}; }}
        """)
        self._drag_pos = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)

        # ---- 설정 톱니바퀴 (클릭 시 바로 설정 뷰로 이동) ----
        self.settings_btn = _AnimatedIconButton("setting.svg")
        self.settings_btn.clicked.connect(self.settingsSelected.emit)
        layout.addWidget(self.settings_btn)

        left_divider = QFrame()
        left_divider.setFrameShape(QFrame.VLine)
        left_divider.setStyleSheet(f"color: {BORDER};")
        left_divider.setFixedHeight(18)
        layout.addWidget(left_divider)

        # ---- 뒤로 / 앞으로 ----
        self.back_btn = _AnimatedIconButton("arrow_back.svg")
        self.back_btn.setEnabled(False)
        self.back_btn.clicked.connect(self.backClicked.emit)

        self.forward_btn = _AnimatedIconButton("arrow_forward.svg")
        self.forward_btn.setEnabled(False)
        self.forward_btn.clicked.connect(self.forwardClicked.emit)

        layout.addWidget(self.back_btn)
        layout.addWidget(self.forward_btn)

        layout.addStretch()
        title_label = QLabel(title)
        title_label.setStyleSheet(f"color:{TEXT_SUB}; font-size:12px; font-weight:600;")
        layout.addWidget(title_label)
        layout.addStretch()

        # ---- 창 컨트롤 ----
        self.min_btn = _AnimatedIconButton("minimize.svg")
        self.min_btn.clicked.connect(self.minimizeClicked.emit)

        self.max_btn = _AnimatedIconButton("zoom_in.svg")
        self.max_btn.clicked.connect(self.maximizeClicked.emit)

        self.close_btn = _AnimatedIconButton("close.svg", hover_color="#FDEDEC")
        self.close_btn.clicked.connect(self.closeClicked.emit)

        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

    # -----------------------------------------------------------------
    # 외부(MainWindow) 헬퍼
    # -----------------------------------------------------------------
    def set_maximized_icon(self, is_maximized: bool):
        filename = "zoom_in.svg" if not is_maximized else "zoom_in.svg"
        self.max_btn.set_icon_file(filename)

    def set_nav_enabled(self, back_enabled: bool, forward_enabled: bool):
        self.back_btn.setEnabled(back_enabled)
        self.forward_btn.setEnabled(forward_enabled)

    # -----------------------------------------------------------------
    # 드래그로 창 이동 / 더블클릭으로 최대화 토글
    # -----------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            window = self.window()
            handle = window.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove"):
                handle.startSystemMove()
                event.accept()
                return
            self._drag_pos = event.globalPosition().toPoint() - window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.maximizeClicked.emit()
        super().mouseDoubleClickEvent(event)
