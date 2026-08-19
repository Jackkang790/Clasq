"""
src/ui/components/title_bar.py

프레임리스 창(Qt.FramelessWindowHint)에서 쓰는 커스텀 상단바.

기능
- 뒤로가기 / 앞으로가기 버튼 (내비게이션 히스토리는 MainWindow가 관리, 여기선 신호만 발생)
- 톱니바퀴 아이콘 -> 클릭 시 아래로 슬라이드되는 드롭다운 패널.
  지금은 "설정하기" 항목 하나만 있고, 클릭하면 settingsSelected 시그널 발생.
- 창 컨트롤: 최소화(-) / 최대화·복원(ㅁ) / 닫기(x)
  실제 페이드/지오메트리 애니메이션은 MainWindow에서 처리하고,
  TitleBar는 클릭 시그널 + 최대화 아이콘 토글만 담당한다.
- 타이틀바를 드래그하면 창 이동, 더블클릭하면 최대화/복원 토글.

MainWindow 쪽 연결 예시는 main.py 주석 참고.
"""
from PySide6.QtCore import Qt, Signal, Property, QPoint, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QPushButton, QHBoxLayout, QVBoxLayout, QLabel, QFrame

PRIMARY = "#6C5CE7"
BORDER = "#E4E6EF"
TEXT_MAIN = "#2D2D3A"
TEXT_SUB = "#8A8CA5"
HOVER_BG = "#F0F0F7"


class _AnimatedIconButton(QPushButton):
    """hover 시 배경색이 부드럽게 전환되는 아이콘 버튼 (min/max/close/gear/뒤로앞으로 공용)."""

    def __init__(self, text, hover_color=HOVER_BG, hover_text_color=None, parent=None):
        super().__init__(text, parent)
        self._normal_color = QColor("#FFFFFF")
        self._hover_color = QColor(hover_color)
        self._bg_color = QColor(self._normal_color)
        self._hover_text_color = hover_text_color
        self._hovering = False

        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 30)
        self.setFlat(True)

        self._anim = QPropertyAnimation(self, b"bgColor", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self._update_style()

    # --- QPropertyAnimation 대상이 되는 커스텀 프로퍼티 ---
    def getBgColor(self):
        return self._bg_color

    def setBgColor(self, color):
        self._bg_color = color
        self._update_style()

    bgColor = Property(QColor, getBgColor, setBgColor)

    def _update_style(self):
        if not self.isEnabled():
            text_color = "#C7C9D9"
        elif self._hovering and self._hover_text_color:
            text_color = self._hover_text_color
        else:
            text_color = TEXT_MAIN
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {self._bg_color.name()};
                color: {text_color};
                border: none;
                border-radius: 6px;
                font-size: 14px;
            }}
        """)

    def _animate_to(self, color):
        self._anim.stop()
        self._anim.setStartValue(self._bg_color)
        self._anim.setEndValue(color)
        self._anim.start()

    def enterEvent(self, event):
        if self.isEnabled():
            self._hovering = True
            self._animate_to(self._hover_color)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        self._animate_to(self._normal_color)
        super().leaveEvent(event)


class _SettingsDropdown(QFrame):
    """톱니바퀴 클릭 시 아래로 슬라이드되는 패널. 지금은 '설정하기' 항목 1개만."""

    itemClicked = Signal()

    def __init__(self, anchor_titlebar):
        # 최상위 윈도우에 얹어서 title bar 높이 아래로도 그려지도록 한다.
        super().__init__(anchor_titlebar.window())
        self._anchor = anchor_titlebar
        self._target_height = 44

        self.setObjectName("settingsDropdown")
        self.setStyleSheet(f"""
            QFrame#settingsDropdown {{
                background-color: white;
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QPushButton#dropdownItem {{
                text-align: left; padding: 8px 14px; border: none;
                background: transparent; color: {TEXT_MAIN}; font-size: 13px;
                border-radius: 6px;
            }}
            QPushButton#dropdownItem:hover {{ background-color: #F5F4FF; color: {PRIMARY}; }}
        """)
        self.setFixedWidth(160)
        self.setMaximumHeight(0)
        self.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)

        item = QPushButton("⚙  설정하기")
        item.setObjectName("dropdownItem")
        item.setCursor(Qt.PointingHandCursor)
        item.clicked.connect(self._on_item_clicked)
        lay.addWidget(item)
        # TODO: 추후 메뉴 항목이 늘어나면 여기에 QPushButton을 더 추가하고
        #       self._target_height를 항목 수에 맞게 늘리면 된다.

        self._anim = QPropertyAnimation(self, b"maximumHeight", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def _on_item_clicked(self):
        self.itemClicked.emit()
        self.close_panel()

    def _reposition(self):
        gear = self._anchor.settings_btn
        anchor_window = self._anchor.window()
        bottom_left = gear.mapTo(anchor_window, QPoint(0, gear.height()))
        x = bottom_left.x() - (self.width() - gear.width())
        y = bottom_left.y() + 4
        self.move(max(8, x), y)

    def open_panel(self):
        self._reposition()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self.maximumHeight())
        self._anim.setEndValue(self._target_height)
        self._anim.start()

    def close_panel(self):
        self._anim.stop()
        self._anim.setStartValue(self.maximumHeight())
        self._anim.setEndValue(0)
        try:
            self._anim.finished.disconnect(self._hide_after_close)
        except (TypeError, RuntimeError):
            pass
        self._anim.finished.connect(self._hide_after_close)
        self._anim.start()

    def _hide_after_close(self):
        if self.maximumHeight() == 0:
            self.hide()


class TitleBar(QWidget):
    """커스텀 상단바. MainWindow에서 프레임리스 창 최상단에 addWidget 한다."""

    backClicked = Signal()
    forwardClicked = Signal()
    minimizeClicked = Signal()
    maximizeClicked = Signal()
    closeClicked = Signal()
    settingsSelected = Signal()

    def __init__(self, title="AI 파일 관리 시스템", parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(44)
        self.setStyleSheet(f"""
            QWidget#titleBar {{ background-color: #FFFFFF; border-bottom: 1px solid {BORDER}; }}
        """)
        self._drag_pos = None
        self._settings_panel_open = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)

        # ---- 뒤로 / 앞으로 ----
        self.back_btn = _AnimatedIconButton("←")
        self.back_btn.setEnabled(False)
        self.back_btn.clicked.connect(self.backClicked.emit)
        self.forward_btn = _AnimatedIconButton("→")
        self.forward_btn.setEnabled(False)
        self.forward_btn.clicked.connect(self.forwardClicked.emit)
        layout.addWidget(self.back_btn)
        layout.addWidget(self.forward_btn)

        layout.addStretch()
        title_label = QLabel(title)
        title_label.setStyleSheet(f"color:{TEXT_SUB}; font-size:12px; font-weight:600;")
        layout.addWidget(title_label)
        layout.addStretch()

        # ---- 설정 톱니바퀴 ----
        self.settings_btn = _AnimatedIconButton("⚙")
        self.settings_btn.clicked.connect(self._toggle_settings_panel)
        layout.addWidget(self.settings_btn)

        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setStyleSheet(f"color: {BORDER};")
        divider.setFixedHeight(18)
        layout.addWidget(divider)

        # ---- 창 컨트롤 ----
        self.min_btn = _AnimatedIconButton("–")
        self.min_btn.clicked.connect(self.minimizeClicked.emit)
        self.max_btn = _AnimatedIconButton("□")
        self.max_btn.clicked.connect(self.maximizeClicked.emit)
        self.close_btn = _AnimatedIconButton("×", hover_color="#FDEDEC", hover_text_color="#E74C3C")
        self.close_btn.clicked.connect(self.closeClicked.emit)

        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

        # ---- 설정 드롭다운 ----
        self._settings_panel = _SettingsDropdown(self)
        self._settings_panel.itemClicked.connect(self._on_settings_item_clicked)

    # -----------------------------------------------------------------
    # 외부(MainWindow)에서 호출하는 헬퍼
    # -----------------------------------------------------------------
    def set_maximized_icon(self, is_maximized: bool):
        self.max_btn.setText("❐" if is_maximized else "□")

    def set_nav_enabled(self, back_enabled: bool, forward_enabled: bool):
        self.back_btn.setEnabled(back_enabled)
        self.forward_btn.setEnabled(forward_enabled)
        self.back_btn._update_style()
        self.forward_btn._update_style()

    # -----------------------------------------------------------------
    # 설정 드롭다운
    # -----------------------------------------------------------------
    def _toggle_settings_panel(self):
        if self._settings_panel_open:
            self._settings_panel.close_panel()
        else:
            self._settings_panel.open_panel()
        self._settings_panel_open = not self._settings_panel_open

    def _on_settings_item_clicked(self):
        self._settings_panel_open = False
        self.settingsSelected.emit()

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