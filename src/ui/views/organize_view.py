"""
src/ui/views/organize_view.py

'정리하기' 화면.
MainWindow의 QStackedWidget에 addWidget(OrganizeView())로 바로 꽂아서 씁니다.

내부적으로 자체 QStackedWidget을 하나 더 가지고 있어서,
  - sub-index 0: 파일 목록 테이블 뷰 (초기 화면)
  - sub-index 1: 자동 그룹화 결과 뷰
두 화면을 오갑니다. (전역 light.qss가 있으면 그걸 우선 따르고,
이 파일의 스타일은 objectName 기반 최소한의 폴백 스타일만 넣었습니다.)
"""
import json
import os
import time
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QStackedWidget, QScrollArea, QAbstractItemView, QFileDialog,
    QMessageBox, QInputDialog, QDialog,
)

from src.utils.app_paths import assets_dir

PRESET_PATH = Path(assets_dir()) / "preset.json"

ICON_GLYPH = {"txt": "📄", "image": "🖼️", "doc": "📄", "default": "📁"}

FALLBACK_QSS = """
QLabel#breadcrumb { color: #8A8CA5; font-size: 12px; }
QLabel#screenTitle { font-size: 20px; font-weight: 800; }
QFrame#infoBanner { background-color: #F1EFFF; border: 1px solid #DCD6FF; border-radius: 8px; }
QLabel#infoBannerText { color: #5A4BD1; font-size: 12px; font-weight: 500; }
QPushButton#primaryBtn {
    background-color: #6C5CE7; color: white; border: none;
    border-radius: 8px; padding: 6px 18px; font-weight: 600; font-size: 13px;
}
QPushButton#primaryBtn:hover { background-color: #5A4BD1; }
QPushButton#secondaryBtn {
    background-color: white; color: #2D2D3A; border: 1px solid #E4E6EF;
    border-radius: 8px; padding: 6px 18px; font-size: 13px;
}
QPushButton#secondaryBtn:hover { background-color: #F0F0F7; }
/* setting_view의 경로삭제(delRoot) 버튼과 동일한 스타일 */
QPushButton#delRoot {
    padding: 8px 16px; border-radius: 8px; background-color: #EF4444;
    color: white; font-weight: bold; border: none;
}
QPushButton#delRoot:hover { background-color: #DC2626; }
QPushButton#delRoot:pressed { background-color: #B91C1C; }
QPushButton#delRoot:disabled { background-color: #F3B5B5; color: #FFFFFF; }
QTableWidget#fileTable {
    background-color: white; border: 1px solid #E4E6EF; border-radius: 10px;
    gridline-color: #E4E6EF; font-size: 13px; color: #2D2D3A;
    alternate-background-color: #F9F9FC;
    selection-background-color: #EFEBFF;
    selection-color: #2D2D3A;
    outline: 0;
}
QTableWidget#fileTable::item { padding: 6px; color: #2D2D3A; }
QTableWidget#fileTable::item:selected {
    background-color: #EFEBFF; color: #2D2D3A;
}
QTableWidget#fileTable QHeaderView::section {
    background-color: #F5F6FA; color: #8A8CA5; padding: 8px;
    border: none; border-bottom: 1px solid #E4E6EF; font-weight: 600;
}
QFrame#groupCard { background-color: #F5F6FA; border: 1px solid #E4E6EF; border-radius: 12px; }
QLabel#groupTitle { font-size: 14px; font-weight: 700; }
QLabel#groupCount { font-size: 11px; color: #8A8CA5; }
QFrame#fileIconCard { background-color: white; border: 1px solid #E4E6EF; border-radius: 10px; }
QLabel#fileIconGlyph { font-size: 22px; }
QLabel#fileIconLabel { font-size: 10px; color: #8A8CA5; }
"""


# ---------------------------------------------------------------------------
# 공용 작은 위젯들
# ---------------------------------------------------------------------------
class _InfoBanner(QFrame):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._tagging_dialog = None
        self.setObjectName("infoBanner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        label = QLabel(text)
        label.setObjectName("infoBannerText")
        self._label = label
        lay.addWidget(label)
        lay.addStretch()

    def set_text(self, text):
        self._label.setText(text)


class _FileIconCard(QFrame):
    def __init__(self, kind="default", label="", parent=None):
        super().__init__(parent)
        self.setObjectName("fileIconCard")
        self.setFixedSize(72, 78)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(4)

        glyph = QLabel(ICON_GLYPH.get(kind, ICON_GLYPH["default"]))
        glyph.setObjectName("fileIconGlyph")
        glyph.setAlignment(Qt.AlignCenter)
        lay.addWidget(glyph)

        text_lbl = QLabel(label)
        text_lbl.setObjectName("fileIconLabel")
        text_lbl.setAlignment(Qt.AlignCenter)
        text_lbl.setWordWrap(True)
        lay.addWidget(text_lbl)


class _GroupedFolderCard(QFrame):
    def __init__(self, folder_name, files, parent=None):
        # files: list[tuple(kind, label)]
        super().__init__(parent)
        self.setObjectName("groupCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(10)

        title_row = QHBoxLayout()
        folder_icon = QLabel("🗂")
        title = QLabel(folder_name)
        title.setObjectName("groupTitle")
        title_row.addWidget(folder_icon)
        title_row.addWidget(title)
        title_row.addStretch()
        count = QLabel(f"{len(files)}개 파일")
        count.setObjectName("groupCount")
        title_row.addWidget(count)
        outer.addLayout(title_row)

        icons_row = QHBoxLayout()
        icons_row.setSpacing(10)
        for kind, label in files:
            icons_row.addWidget(_FileIconCard(kind, label))
        icons_row.addStretch()
        outer.addLayout(icons_row)


class _HistoryDialog(QDialog):
    """최근 파일 정리 이력을 표시하고 Undo를 제공하는 다이얼로그."""

    undoRequested = Signal(str)  # operation_id

    _DIALOG_QSS = """
        QDialog { background: #FFFFFF; }
        QLabel { color: #2D2D3A; font-size: 13px; }
        QLabel#dlgTitle { font-size: 16px; font-weight: 700; color: #2D2D3A; }
        QLabel#dlgDesc  { font-size: 12px; color: #8A8CA5; }
        QTableWidget {
            background: #FFFFFF; border: 1px solid #E4E6EF;
            border-radius: 10px; gridline-color: #F0F0F7;
            font-size: 13px; color: #2D2D3A; outline: 0;
            alternate-background-color: #F9F9FC;
            selection-background-color: #EFEBFF;
        }
        QTableWidget::item { padding: 0 10px; }
        QHeaderView::section {
            background: #F5F6FA; color: #8A8CA5; font-size: 12px;
            font-weight: 600; border: none; border-bottom: 1px solid #E4E6EF;
            padding: 6px 10px;
        }
        QPushButton#undoBtn {
            background: #6C5CE7; color: white; border: none;
            border-radius: 6px; font-weight: 600; font-size: 12px;
        }
        QPushButton#undoBtn:hover { background: #5A4BD1; }
        QPushButton#closeBtn {
            background: #F5F6FA; color: #2D2D3A; border: 1px solid #E4E6EF;
            border-radius: 8px; padding: 8px 24px; font-size: 13px; font-weight: 600;
        }
        QPushButton#closeBtn:hover { background: #EFEBFF; color: #6C5CE7; }
    """

    def __init__(self, operations: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("정리 이력")
        self.setMinimumWidth(660)
        self.setMinimumHeight(360)
        self.setStyleSheet(self._DIALOG_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("정리 이력")
        title.setObjectName("dlgTitle")
        layout.addWidget(title)

        desc = QLabel("Undo를 누르면 이동된 파일이 원위치로 돌아갑니다.")
        desc.setObjectName("dlgDesc")
        layout.addWidget(desc)

        table = QTableWidget(len(operations), 4)
        table.setHorizontalHeaderLabels(["날짜/시간", "이동 파일", "상태", "동작"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        table.setColumnWidth(3, 90)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(48)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)

        for row, op in enumerate(operations):
            for col, text in enumerate([
                (op["applied_at"] or "")[:19],
                f"{op['file_count']}개",
                "정리 완료" if (op["applied_count"] or 0) > 0 else "되돌림 완료",
            ]):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                table.setItem(row, col, item)

            can_undo = (op["applied_count"] or 0) > 0
            if can_undo:
                btn = QPushButton("Undo")
                btn.setObjectName("undoBtn")
                btn.setFixedHeight(32)
                btn.setCursor(Qt.PointingHandCursor)
                oid = op["operation_id"]
                btn.clicked.connect(lambda checked, o=oid: self._request_undo(o))
                container = QWidget()
                cl = QHBoxLayout(container)
                cl.setContentsMargins(8, 8, 8, 8)
                cl.addWidget(btn)
                table.setCellWidget(row, 3, container)
            else:
                item = QTableWidgetItem("—")
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, 3, item)

        layout.addWidget(table, stretch=1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("닫기")
        close_btn.setObjectName("closeBtn")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def _request_undo(self, operation_id: str):
        self.undoRequested.emit(operation_id)
        self.accept()


def _make_btn(text, primary=False, danger=False):
    btn = QPushButton(text)
    if danger:
        # setting_view의 경로삭제 버튼(objectName=delRoot) 디자인을 그대로 재사용한다.
        btn.setObjectName("delRoot")
    else:
        btn.setObjectName("primaryBtn" if primary else "secondaryBtn")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setMinimumHeight(36)
    return btn


# ---------------------------------------------------------------------------
# sub-screen 0: 파일 목록 테이블
# ---------------------------------------------------------------------------
class _FileTableScreen(QWidget):
    autoOrganizeRequested = Signal()
    addPathRequested = Signal(str)
    presetLoadRequested = Signal()
    removePathRequested = Signal()
    historyRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        breadcrumb = QLabel("메인화면 > 정리하기 ...")
        breadcrumb.setObjectName("breadcrumb")
        root.addWidget(breadcrumb)

        header_row = QHBoxLayout()
        title = QLabel("파일 자동 정리")
        title.setObjectName("screenTitle")
        header_row.addWidget(title)
        header_row.addStretch()

        preset_btn = _make_btn("프리셋 불러오기")
        preset_btn.clicked.connect(self.presetLoadRequested.emit)
        add_path_btn = _make_btn("경로 추가")
        add_path_btn.clicked.connect(self._on_add_path)
        remove_path_btn = _make_btn("경로 삭제", danger=True)
        remove_path_btn.clicked.connect(self.removePathRequested.emit)
        history_btn = _make_btn("정리 이력")
        history_btn.clicked.connect(self.historyRequested.emit)
        auto_btn = _make_btn("자동정리", primary=True)
        auto_btn.clicked.connect(self.autoOrganizeRequested.emit)
        self.auto_btn = auto_btn
        header_row.addWidget(preset_btn)
        header_row.addWidget(add_path_btn)
        header_row.addWidget(remove_path_btn)
        header_row.addWidget(history_btn)
        header_row.addWidget(auto_btn)
        root.addLayout(header_row)

        target_hint = QLabel(
            "정리 대상 파일과 현재 위치입니다. 실제 정리 결과는 '자동정리' 후 Preview에서 확인합니다."
        )
        target_hint.setObjectName("descriptionText")
        root.addWidget(target_hint)

        self.table = QTableWidget(0, 3)
        self.table.setObjectName("fileTable")
        self.table.setHorizontalHeaderLabels(["파일명", "태그", "파일 경로"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        # 외부(app 전역) QSS에 테이블 선택색이 이미 정의돼 있어도 밀리지 않도록
        # 팔레트를 직접 덮어써서 이중으로 고정한다.
        palette = self.table.palette()
        palette.setColor(QPalette.Highlight, QColor("#EFEBFF"))
        palette.setColor(QPalette.HighlightedText, QColor("#2D2D3A"))
        palette.setColor(QPalette.Inactive, QPalette.Highlight, QColor("#EFEBFF"))
        palette.setColor(QPalette.Inactive, QPalette.HighlightedText, QColor("#2D2D3A"))
        self.table.setPalette(palette)
        root.addWidget(self.table, stretch=1)

        self._load_mock_data()

    def _load_mock_data(self):
        rows = [
            ("보고서_최종.docx", "문서, 업무", "C:/Users/Downloads/보고서_최종.docx"),
            ("스크린샷_0812.png", "이미지, 캡처", "C:/Users/Downloads/스크린샷_0812.png"),
            ("회의록.txt", "문서, 메모", "C:/Users/Downloads/회의록.txt"),
        ]
        self.set_rows(rows)

    def set_rows(self, rows):
        """rows: list[tuple(파일명, 태그, 파일경로)] - REQ-004 파일 목록 표시"""
        self.table.setRowCount(0)
        for r, (name, tag, path) in enumerate(rows):
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(name))
            self.table.setItem(r, 1, QTableWidgetItem(tag))
            self.table.setItem(r, 2, QTableWidgetItem(path))

    def selected_rows(self):
        """사용자가 선택한 행 번호를 내림차순으로 반환합니다. (기존 다중 선택 방식 그대로 사용)"""
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()}, reverse=True)

    def remove_rows(self, rows):
        """지정한 행을 표에서만 제거합니다. (DB·preset.json·실제 파일은 건드리지 않음)"""
        for row in rows:
            self.table.removeRow(row)

    def _on_add_path(self):
        path = QFileDialog.getExistingDirectory(self, "정리할 폴더 선택")
        if path:
            self.addPathRequested.emit(path)


# ---------------------------------------------------------------------------
# sub-screen 1: 자동 그룹화 결과
# ---------------------------------------------------------------------------
class _GroupedScreen(QWidget):
    organizeConfirmed = Signal()
    editRequested = Signal()
    tagUntaggedRequested = Signal()
    manualTagRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        breadcrumb = QLabel("메인화면 > 정리하기 > 자동그룹화 ...")
        breadcrumb.setObjectName("breadcrumb")
        root.addWidget(breadcrumb)

        self._info_banner = _InfoBanner("AI 분석 완료 - 생성된 최적의 파일 정리 계획입니다")
        root.addWidget(self._info_banner)

        header_row = QHBoxLayout()
        title = QLabel("파일 자동 정리")
        title.setObjectName("screenTitle")
        header_row.addWidget(title)
        header_row.addStretch()
        root.addLayout(header_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }

            QScrollBar:vertical {
                border: none;
                background-color: transparent;
                width: 8px;
                margin: 0px 0px 0px 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background-color: #CBD5E1;
                min-height: 30px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #94A3B8;
            }
            QScrollBar::handle:vertical:pressed {
                background-color: #6C5CE7;
            }
            QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {
                border: none;
                background: none;
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        self.group_container = QWidget()
        self.group_layout = QVBoxLayout(self.group_container)
        self.group_layout.setSpacing(12)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        self.group_layout.addStretch()
        scroll.setWidget(self.group_container)
        root.addWidget(scroll, stretch=1)

        self._load_mock_groups()

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        edit_btn = _make_btn("수정하기")
        edit_btn.clicked.connect(self.editRequested.emit)
        self.confirm_btn = _make_btn("이대로 정리하기", primary=True)
        self.confirm_btn.clicked.connect(self.organizeConfirmed.emit)
        bottom_row.addWidget(edit_btn)
        bottom_row.addWidget(self.confirm_btn)
        root.addLayout(bottom_row)

    def _load_mock_groups(self):
        groups = [
            ("문서 폴더", [("txt", "회의록.txt"), ("doc", "보고서.docx")]),
            ("이미지 폴더", [("image", "스크린샷_0812.png"), ("image", "사진1.jpg"), ("image", "사진2.jpg")]),
        ]
        self.set_groups(groups)

    def set_groups(self, groups):
        """groups: list[tuple(폴더명, list[tuple(kind, label)])] - REQ-010 그룹화 결과 표시"""
        while self.group_layout.count() > 1:
            item = self.group_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for name, files in groups:
            card = _GroupedFolderCard(name, files)
            # 미분류 카드에는 AI 태깅 버튼을 추가한다
            if name.startswith("미분류 (AI 태그 없음)"):
                manual_btn = _make_btn("수동 태그 지정", primary=True)
                manual_btn.clicked.connect(self.manualTagRequested.emit)
                card.layout().addWidget(manual_btn)
            self.group_layout.insertWidget(self.group_layout.count() - 1, card)

    def set_banner_text(self, text):
        self._info_banner.set_text(text)

    def set_confirm_enabled(self, enabled: bool):
        self.confirm_btn.setEnabled(enabled)


# ---------------------------------------------------------------------------
# 외부에 노출되는 진짜 뷰: OrganizeView
# ---------------------------------------------------------------------------
class OrganizeView(QWidget):
    """기존 정리 UI에 DB·AI 태깅·실제 파일 이동 기능을 연결하는 뷰."""

    def __init__(self, core=None, refresh_manager=None, parent=None):
        super().__init__(parent)
        self.core = core
        self.refresh_manager = refresh_manager
        self.grouped_files = {}
        self.organize_base_path = ""
        self._tagging_worker = None
        self._inventory_worker = None
        self._inventory_context = ""
        self._auto_destination = ""
        self._tagging_context = ""
        self._tagging_started_at = 0.0
        self._tagging_target_count = 0
        self._analysis_seconds_per_file = None
        self._plan_worker = None
        self._plan_dialog = None
        self._apply_worker = None
        self._apply_dialog = None
        self._last_plan: dict = {}
        self._last_plan_files: list = []
        self._last_untagged_files: list = []
        self._preview_base_path: str = ""
        self._preview_move_plan: list = []
        self._preview_conflicts: list = []
        self._untagged_worker = None
        self._untagged_dialog = None
        self._analysis_attempted: set = set()
        # Batch 12: Plan context tracking (stale result 방지)
        self._plan_context_id: int = 0
        self._analysis_plan_context_id: int = -1
        # Batch 13: Undo/History
        self._undo_worker = None
        self._undo_dialog = None
        self.setObjectName("organizeView")
        self.setStyleSheet(FALLBACK_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._inner_stack = QStackedWidget()
        self._table_screen = _FileTableScreen()
        self._grouped_screen = _GroupedScreen()
        self._inner_stack.addWidget(self._table_screen)
        self._inner_stack.addWidget(self._grouped_screen)
        layout.addWidget(self._inner_stack)

        self._table_screen.autoOrganizeRequested.connect(self._on_auto_organize)
        self._table_screen.addPathRequested.connect(self._on_path_added)
        self._table_screen.presetLoadRequested.connect(self._on_load_preset)
        self._table_screen.removePathRequested.connect(self._on_remove_path)
        self._table_screen.historyRequested.connect(self._show_history_dialog)
        self._grouped_screen.editRequested.connect(self._show_table)
        self._grouped_screen.organizeConfirmed.connect(self._on_organize_confirmed)
        self._grouped_screen.manualTagRequested.connect(self._manual_tag_unclassified)
        if self.core:
            self._load_files_from_db()

    def _show_grouped(self):
        self._inner_stack.setCurrentWidget(self._grouped_screen)

    def _show_table(self):
        self._inner_stack.setCurrentWidget(self._table_screen)

    def _load_files_from_db(self):
        try:
            managed = [
                os.path.normcase(os.path.abspath(p))
                for p in self.core.registry.get_managed_paths()
            ]
            rows = []
            for file_info in self.core.get_all_files():
                file_path = file_info["file_path"]
                norm = os.path.normcase(os.path.abspath(file_path))
                # managed_paths 하위에 있는 파일만 표시
                if managed and not any(norm.startswith(m + os.sep) or norm == m for m in managed):
                    continue
                tags = file_info.get("tags", "")
                if isinstance(tags, list):
                    tags = ", ".join(tags)
                rows.append((file_info["file_name"], tags or "", file_path))
            self._table_screen.set_rows(rows)
        except Exception as exc:
            QMessageBox.critical(self, "파일 목록 오류", f"파일 목록을 불러오지 못했습니다.\n{exc}")

    def _on_remove_path(self):
        """선택한 항목을 이번 정리 대상에서만 제외합니다.

        DB(`file_manager.db`, `managed_paths`)·`assets/preset.json`·실제 파일은 그대로 둔다.
        """
        rows = self._table_screen.selected_rows()
        if not rows:
            QMessageBox.information(self, "경로 삭제", "삭제할 경로를 선택해주세요.")
            return

        self._table_screen.remove_rows(rows)

    def _read_presets(self):
        """assets/preset.json의 presets 배열을 읽습니다. (프리셋 조회에 DB를 사용하지 않음)

        반환값: (프리셋 목록, 오류 메시지). 파일이 없거나 비어 있으면 빈 목록을,
        JSON 파싱에 실패하면 예외 없이 오류 메시지를 돌려준다.
        """
        if not PRESET_PATH.exists():
            return [], None

        try:
            with open(PRESET_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return [], "프리셋을 불러올 수 없습니다.\npreset.json 파일을 확인해주세요."

        presets = data.get("presets", []) if isinstance(data, dict) else []
        return [
            preset for preset in presets
            if isinstance(preset, dict) and str(preset.get("preset_name", "")).strip()
        ], None

    def _on_load_preset(self):
        """assets/preset.json의 preset_name을 보여주고 선택한 프리셋을 정리 대상에 적용합니다."""
        presets, error = self._read_presets()
        if error:
            QMessageBox.warning(self, "프리셋 불러오기", error)
            return

        if not presets:
            QMessageBox.information(self, "프리셋 불러오기", "프리셋 없음")
            return

        names = [str(preset["preset_name"]) for preset in presets]
        selected, ok = QInputDialog.getItem(
            self, "프리셋 불러오기", "프리셋을 선택하세요:", names, 0, False
        )
        if not ok or not selected:
            return

        preset = next(p for p in presets if str(p["preset_name"]) == selected)
        self._apply_preset(preset)

    def _apply_preset(self, preset):
        """선택한 프리셋의 targets(type/path/extensions)를 정리 대상 목록에 반영합니다."""
        preset_extensions = self._normalize_extensions(preset.get("extensions"))
        rows = self._current_table_rows()
        known_paths = {row[2] for row in rows}
        added, missing = 0, []

        for target in preset.get("targets", []):
            if not isinstance(target, dict):
                continue
            path = str(target.get("path", "")).strip()
            if not path:
                continue
            if not os.path.exists(path):
                missing.append(path)
                continue

            extensions = self._normalize_extensions(target.get("extensions")) or preset_extensions
            for file_path, file_name in self._collect_target_files(target, path, extensions):
                if file_path in known_paths:
                    continue
                known_paths.add(file_path)
                rows.append((file_name, "", file_path))
                added += 1

        self._table_screen.set_rows(rows)
        self._show_table()

        message = f"'{preset['preset_name']}' 프리셋에서 {added}개 파일을 정리 대상에 추가했습니다."
        if missing:
            message += "\n\n다음 경로를 찾지 못해 건너뛰었습니다:\n" + "\n".join(missing)
        QMessageBox.information(self, "프리셋 불러오기", message)

    def _collect_target_files(self, target, path, extensions):
        """target type에 따라 파일 하나 또는 폴더 하위 파일을 (경로, 이름)으로 모읍니다."""
        if target.get("type") == "file":
            if extensions and not path.lower().endswith(extensions):
                return []
            return [(path, os.path.basename(path))]

        if not self.core:
            return []
        return [
            (file_info["file_path"], file_info["file_name"])
            for file_info in self.core.scan_directory_files(path)
            if not extensions or file_info["file_path"].lower().endswith(extensions)
        ]

    @staticmethod
    def _normalize_extensions(extensions):
        if not isinstance(extensions, list):
            return ()
        return tuple(ext.lower() for ext in extensions if isinstance(ext, str) and ext.strip())

    def _current_table_rows(self):
        """현재 테이블에 표시된 행을 (파일명, 태그, 경로) 튜플 목록으로 반환합니다."""
        table = self._table_screen.table
        return [
            (table.item(row, 0).text(), table.item(row, 1).text(), table.item(row, 2).text())
            for row in range(table.rowCount())
        ]

    def _on_path_added(self, path):
        """Register, scan, then offer Qwen tagging for only new/stale files."""
        if not self.core:
            QMessageBox.information(self, "경로 추가됨", f"다음 폴더가 정리 대상에 추가되었습니다:\n{path}")
            return
 
        # DB에 경로 추가
        result = self.core.registry.add_managed_path(path)
        if not result.get("success"):
            QMessageBox.warning(self, "경로 추가 실패", result.get("message", "경로를 추가하지 못했습니다."))
            return
 
        # 파일 스캔
        try:
            scanned_files = self.core.scan_directory_files(path)
 
            if not scanned_files:
                QMessageBox.information(self, "경로 추가됨", 
                    f"경로가 추가되었지만 지원되는 파일이 없습니다:\n{path}")
                return
 
            # 스캔된 파일들을 테이블에 추가
            current_rows = self._current_table_rows()
 
            # 새로운 파일들 추가
            for file_info in scanned_files:
                # 중복 체크
                if not any(row[2] == file_info["file_path"] for row in current_rows):
                    tags_str = ", ".join(file_info.get("tags", []))
                    current_rows.append((
                        file_info["file_name"],
                        tags_str,
                        file_info["file_path"]
                    ))
 
            # 아직 태깅하지 않은 파일은 DB 레코드가 없으므로 이 표에 유지한다.
            # 태깅 완료 시에만 DB 모델 전체를 다시 읽는다.
            self._table_screen.set_rows(current_rows)
 
            self._start_incremental_inventory(
                file_paths=[item["file_path"] for item in scanned_files],
                context="path_add",
            )
 
        except Exception as e:
            QMessageBox.critical(self, "오류", f"파일 스캔 중 오류가 발생했습니다:\n{str(e)}")
 
    def _start_incremental_inventory(self, *, context, folders=None, file_paths=None):
        if self._inventory_worker and self._inventory_worker.isRunning():
            QMessageBox.information(self, "파일 확인", "이미 파일 변경 여부를 확인하고 있습니다.")
            return
        from src.utils.workers import IncrementalInventoryWorker
        from src.ui.widgets.progress_dialog import TaskProgressDialog

        self._inventory_context = context
        self._plan_dialog = TaskProgressDialog(
            "파일 확인 중",
            "기존 분석 결과와 새로 분석할 파일을 구분하고 있습니다.",
            parent=self,
            unit="파일",
        )
        self._inventory_worker = IncrementalInventoryWorker(
            folder_paths=folders,
            file_paths=file_paths,
            db_path=getattr(self.core, "db_path", "file_manager.db"),
        )
        self._inventory_worker.progress.connect(self._on_plan_progress)
        self._inventory_worker.completed.connect(self._on_inventory_completed)
        self._inventory_worker.error.connect(self._on_inventory_error)
        self._inventory_worker.finished.connect(self._on_inventory_thread_finished)
        self._inventory_worker.start()
        self._plan_dialog.show()

    def _materialize_inventory_records(self, plan):
        """Persist reuse/untagged records without moving any user file."""
        for item in plan.get("same_content", []):
            self.core.registry.register_reused_analysis(
                item["file_path"], item["source_file_path"], item["file_hash"]
            )
        failures = []
        # Only NEW files need a placeholder row. Existing untagged/changed
        # records keep the last successful metadata and fingerprint intact.
        for item in plan.get("new", []):
            result = self.core.registry.register_unanalyzed_file(item["file_path"])
            if not result.get("success"):
                failures.append(item["file_path"])
        return failures

    def _on_inventory_completed(self, plan):
        self._close_plan_dialog()
        context = self._inventory_context
        self._inventory_context = ""
        self._materialize_inventory_records(plan)
        self._load_files_from_db()
        self._refresh_database_views()
        pending = [item["file_path"] for item in plan.get("pending", [])]
        counts = plan.get("counts", {})

        if context == "path_add":
            if not pending:
                QMessageBox.information(
                    self, "경로 추가됨",
                    f"{counts.get('scanned', 0):,}개 파일을 확인했습니다. "
                    "기존 태그를 그대로 재사용합니다.",
                )
                return
            reply = QMessageBox.question(
                self,
                "AI 태깅",
                f"새로 발견되었거나 변경된 {len(pending):,}개 파일에 "
                "AI 태깅을 진행할까요?\n\n"
                "아니요를 선택해도 파일은 목록에 남으며 나중에 다시 태깅할 수 있습니다.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._start_ai_tagging(pending, context="path_add")
            return

        if context != "auto_organize":
            return

        reused = (
            counts.get("already_analyzed", 0)
            + counts.get("same_content", 0)
        )
        eta = self._format_analysis_eta(len(pending))
        summary = (
            f"총 파일: {counts.get('scanned', 0):,}개\n"
            f"기존 결과 재사용: {reused:,}개\n"
            f"새 파일: {counts.get('new', 0):,}개\n"
            f"변경 파일: {counts.get('changed', 0):,}개\n"
            f"미태깅/분석 필요: {len(pending):,}개\n\n"
            f"예상 분석 시간: {eta}"
        )
        if pending:
            reply = QMessageBox.question(
                self, "파일 자동정리", summary + "\n\n분석 후 Preview를 생성할까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._last_plan = plan
                self._last_plan_files = plan.get("scanned", [])
                self._start_ai_tagging(pending, context="auto_organize")
                return
        else:
            QMessageBox.information(self, "파일 자동정리", summary)
        self._on_plan_completed(plan)

    def _on_inventory_error(self, message):
        self._close_plan_dialog()
        self._inventory_context = ""
        self._auto_destination = ""
        QMessageBox.warning(self, "파일 확인 오류", str(message))

    def _on_inventory_thread_finished(self):
        worker = self._inventory_worker
        self._inventory_worker = None
        if worker:
            worker.deleteLater()

    def _start_ai_tagging(self, paths, context="path_add"):
        if self._tagging_worker and self._tagging_worker.isRunning():
            QMessageBox.information(self, "AI 태깅", "이미 태깅 작업이 진행 중입니다.")
            return
        from src.utils.workers import FolderScanAndTagWorker
        from src.ui.widgets.progress_dialog import TaskProgressDialog
        self._tagging_context = context
        self._tagging_started_at = time.monotonic()
        self._tagging_target_count = len(paths)
        self._tagging_worker = FolderScanAndTagWorker(paths, self.core)

        self._tagging_dialog = TaskProgressDialog(
            "AI 태깅 중", "AI 태깅을 준비하고 있습니다...", parent=self, unit="파일"
        )

        self._tagging_worker.progress.connect(self._tagging_dialog.setLabelText)
        self._tagging_worker.fileCompleted.connect(self._on_tagging_progress)
        self._tagging_worker.finished.connect(self._on_tagging_finished)
        self._tagging_worker.error.connect(self._on_tagging_error)

        self._tagging_worker.start()
        self._tagging_dialog.show()

    def _on_tagging_finished(self, summary=None):
        if self._tagging_dialog is not None:
            self._tagging_dialog.close()
            self._tagging_dialog = None
        self._load_files_from_db()
        self._refresh_database_views()
        summary = summary or {}
        elapsed = max(0.0, time.monotonic() - self._tagging_started_at)
        processed = max(1, summary.get("total", self._tagging_target_count))
        observed = elapsed / processed
        if self._analysis_seconds_per_file is None:
            self._analysis_seconds_per_file = observed
        else:
            self._analysis_seconds_per_file = self._analysis_seconds_per_file * 0.7 + observed * 0.3
        context = self._tagging_context
        self._tagging_context = ""
        QMessageBox.information(
            self, "AI 태깅",
            f"AI 태깅 완료: 성공 {summary.get('success', 0)}개, "
            f"실패 {len(summary.get('failed', []))}개",
        )
        if context == "auto_organize" and self._auto_destination:
            self._start_incremental_inventory(
                context="auto_organize", folders=self._get_target_folders()
            )

    def _on_tagging_error(self, message):
        if self._tagging_dialog is not None:
            self._tagging_dialog.close()
            self._tagging_dialog = None
        self._tagging_context = ""
        self._auto_destination = ""
        self._load_files_from_db()
        self._refresh_database_views()
        QMessageBox.critical(self, "AI 태깅 오류", message)

    def _on_tagging_progress(self, current, total, file_name):
        if not self._tagging_dialog:
            return
        elapsed = max(0.0, time.monotonic() - self._tagging_started_at)
        average = elapsed / current if current > 0 else self._analysis_seconds_per_file
        remaining = max(0, total - current)
        eta = self._format_analysis_eta(remaining, average)
        self._tagging_dialog.update_progress(
            current, total, file_name, status=f"AI 분석 중 · 남은 예상 시간 {eta}"
        )

    def _format_analysis_eta(self, count, seconds_per_file=None):
        from src.utils.workers import estimate_analysis_eta
        return estimate_analysis_eta(
            count,
            seconds_per_file if seconds_per_file is not None else self._analysis_seconds_per_file,
        )

    def _on_auto_organize(self):
        if not self.core:
            QMessageBox.warning(self, "자동 정리", "코어 시스템이 초기화되지 않았습니다.")
            return

        # 중복 실행 방지
        if (
            (self._inventory_worker and self._inventory_worker.isRunning())
            or (self._tagging_worker and self._tagging_worker.isRunning())
            or (self._plan_worker and self._plan_worker.isRunning())
        ):
            QMessageBox.information(self, "자동 정리", "이미 분석 작업이 진행 중입니다.")
            return

        # 대상 폴더 추출 및 검증
        folders = self._get_target_folders()
        if not folders:
            QMessageBox.warning(
                self, "자동 정리",
                "분석할 폴더를 찾을 수 없습니다.\n'경로 추가'로 실제 폴더를 추가해 주세요."
            )
            return

        self._clear_pending_preview()
        destination = QFileDialog.getExistingDirectory(self, "정리 결과를 저장할 폴더 선택")
        if not destination:
            return
        self._auto_destination = destination
        self._start_incremental_inventory(context="auto_organize", folders=folders)

    def _get_target_folders(self):
        """사용자가 등록한 managed_paths를 스캔 대상으로 반환한다.

        정리 완료 후 테이블엔 정리된 경로(=/정리폴더/문서/)가 표시되므로
        테이블 행에서 부모 폴더를 추출하면 정리 결과 폴더가 재스캔 대상이 된다.
        managed_paths(사용자가 '경로 추가'로 등록한 원본 폴더)를 사용해야
        이미 정리된 파일이 다음 자동정리에서 다시 대상으로 잡히지 않는다.
        """
        try:
            if self.core:
                paths = self.core.registry.get_managed_paths()
                return [
                    p for p in paths
                    if Path(p).is_dir() and os.access(p, os.R_OK)
                ]
        except Exception:
            pass
        # fallback: managed_paths를 읽을 수 없을 때만 테이블 행에서 파생
        seen = set()
        folders = []
        for _, _, file_path in self._current_table_rows():
            if not file_path:
                continue
            parent = str(Path(file_path).parent)
            if parent in seen:
                continue
            seen.add(parent)
            try:
                if Path(parent).is_dir() and os.access(parent, os.R_OK):
                    folders.append(parent)
            except (OSError, ValueError):
                pass
        return folders

    def _on_plan_progress(self, message):
        if self._plan_dialog:
            self._plan_dialog.setLabelText(message)

    def _close_plan_dialog(self):
        if self._plan_dialog:
            self._plan_dialog.close()
            self._plan_dialog = None
        self._table_screen.auto_btn.setEnabled(True)

    def _on_plan_completed(self, plan):
        self._close_plan_dialog()
        # 새 Plan이 생성될 때마다 context id 증가 → stale AI 결과 감지용
        self._plan_context_id += 1

        counts = plan.get("counts", {})
        scanned_count = counts.get("scanned", 0)
        new_count = counts.get("new", 0)
        text_idx = plan.get("text_index", {})
        txt_indexed = text_idx.get("indexed", 0)

        self._last_plan = plan
        self._last_plan_files = plan.get("scanned", [])

        # Batch 10: 미분류 파일(AI 태그 없음) 감지 및 표시
        untagged = self._get_untagged_from_plan()
        self._last_untagged_files = untagged
        tagged_count = len(self._last_plan_files) - len(untagged)

        plan_file_set = {
            os.path.normcase(os.path.abspath(path))
            for path in self._last_plan_files
        }
        unresolved_paths = {
            os.path.normcase(os.path.abspath(item["file_path"]))
            for item in plan.get("pending", [])
        }
        all_tagged = self.core.get_files_for_organize()
        organize_files = [
            file_info for file_info in all_tagged
            if (
                os.path.normcase(os.path.abspath(file_info["file_path"])) in plan_file_set
                and os.path.normcase(os.path.abspath(file_info["file_path"]))
                not in unresolved_paths
            )
        ]
        grouped_files = self.core.group_files_by_tags(organize_files)

        base_path = self._auto_destination
        if not base_path:
            base_path = QFileDialog.getExistingDirectory(self, "정리 결과를 저장할 폴더 선택")
        if not base_path:
            self._clear_pending_preview()
            self._show_table()
            return
        # 미분류만 있는 경우에도 destination을 저장해 태깅 완료 후 rebuild에 사용한다
        self._preview_base_path = base_path
        self._auto_destination = ""

        if not grouped_files:
            groups_ui = []
            if untagged:
                groups_ui.append((
                    f"미분류 (AI 태그 없음) — {len(untagged)}개",
                    [
                        (
                            self._get_file_kind_by_extension(path),
                            (Path(path).name[:15] + "...")
                            if len(Path(path).name) > 15 else Path(path).name,
                        )
                        for path in untagged[:10]
                    ],
                ))
            else:
                groups_ui.append(("정리 가능한 파일 없음", []))
            self._grouped_screen.set_banner_text(
                f"분석 완료 — {scanned_count:,}개 파일 스캔 | "
                "태그가 설정된 정리 대상이 없습니다. 파일은 변경되지 않았습니다."
            )
            self._grouped_screen.set_groups(groups_ui)
            self._grouped_screen.set_confirm_enabled(False)
            self._show_grouped()
            return

        # 실제 파일 시스템을 변경하지 않고, 승인 후 생성될 대상 구조만 계산한다.
        preview = self.core.build_organize_preview(grouped_files, base_path)
        files_by_path = {
            os.path.normcase(os.path.abspath(file_info["file_path"])): file_info
            for file_info in organize_files
        }
        groups_by_tag: dict = {}
        move_plan = []
        conflicts = []
        for item in preview:
            label = item["file_name"]
            if item["has_conflict"]:
                label = f"{label} (충돌로 제외)"
                conflicts.append(item)
            else:
                file_entry = files_by_path.get(
                    os.path.normcase(os.path.abspath(item["source_path"]))
                )
                if file_entry:
                    move_plan.append({
                        "file_id": file_entry["id"],
                        "file_path": item["source_path"],
                        "target_path": item["target_path"],
                        "file_name": item["file_name"],
                    })
            groups_by_tag.setdefault(item["tag"], []).append((
                self._get_file_kind_by_extension(item["source_path"]),
                label,
            ))

        self._preview_move_plan = move_plan
        self._preview_conflicts = conflicts
        groups_ui = [(tag, files[:10]) for tag, files in groups_by_tag.items()]

        if untagged:
            banner_text = (
                f"분석 완료 — {scanned_count:,}개 파일 스캔 | "
                f"태그 있음: {tagged_count:,}개 | "
                f"미분류(AI 태그 없음): {len(untagged):,}개 | "
                f"충돌 제외: {len(conflicts):,}개 | 파일은 아직 변경되지 않습니다."
            )
            untagged_cards = [
                (
                    self._get_file_kind_by_extension(f),
                    (Path(f).name[:15] + "...") if len(Path(f).name) > 15 else Path(f).name,
                )
                for f in untagged[:10]
            ]
            groups_ui.append((
                f"미분류 (AI 태그 없음) — {len(untagged)}개",
                untagged_cards,
            ))
        else:
            banner_text = (
                f"분석 완료 — {scanned_count:,}개 파일 스캔 | "
                f"신규 {new_count:,}개, 색인 {txt_indexed:,}개 갱신 | "
                f"충돌 제외: {len(conflicts):,}개 | 파일은 아직 변경되지 않습니다."
            )

        self._grouped_screen.set_banner_text(banner_text)
        self._grouped_screen.set_groups(groups_ui)  # noqa: F821 (banner_text always set above)
        # 실제 이동 가능한 Preview 항목이 있을 때만 Apply를 허용한다.
        if move_plan:
            self._grouped_screen.set_confirm_enabled(True)
        else:
            self._grouped_screen.set_confirm_enabled(False)
        self._show_grouped()

    def _on_plan_error(self, message):
        self._close_plan_dialog()
        safe_msg = message.split("\n")[0][:200] if message else "알 수 없는 오류"
        QMessageBox.critical(self, "자동 정리 오류", safe_msg)

    def _on_organize_confirmed(self):
        """'이대로 정리하기' 버튼 — Apply 흐름 시작."""
        if not self.core:
            QMessageBox.warning(self, "파일 정리", "코어 시스템이 초기화되지 않았습니다.")
            return

        # 중복 Apply 방지
        if self._apply_worker and self._apply_worker.isRunning():
            QMessageBox.information(self, "파일 정리", "이미 파일 정리 작업이 진행 중입니다.")
            return

        if self._undo_worker and self._undo_worker.isRunning():
            QMessageBox.information(self, "파일 정리", "되돌리기 작업 중에는 새 정리를 적용할 수 없습니다.")
            return

        # Preview에 포함됐고 현재도 태그가 유지된 파일만 동일한 목적지로 적용한다.
        current_tagged_paths = {
            os.path.normcase(os.path.abspath(file_info["file_path"]))
            for file_info in self.core.get_files_for_organize()
        }
        move_plan = [
            item for item in self._preview_move_plan
            if os.path.normcase(os.path.abspath(item["file_path"])) in current_tagged_paths
        ]
        untagged = self._get_untagged_from_plan()
        self._last_untagged_files = untagged

        if not self._preview_base_path or not move_plan:
            QMessageBox.information(
                self, "파일 정리",
                f"적용할 정리 Preview가 없습니다.\n"
                f"미태깅 파일: {len(untagged)}개\n\n"
                "'자동정리'를 다시 실행해 정리 계획을 확인해 주세요.",
            )
            return

        # 태그 있는 파일과 미분류 파일이 혼재하는 경우
        if untagged:
            reply = QMessageBox.question(
                self, "파일 정리",
                f"Preview에 포함된 저장목록의 태그가 설정된 {len(move_plan)}개 파일만 정리합니다.\n"
                f"미태깅 파일 {len(untagged)}개는 제외됩니다. "
                "필요하면 저장목록에서 태그를 설정해 주세요. 계속할까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # 최종 확인 Dialog
        conflict_note = (
            f"\n충돌(이미 존재 → 제외): {len(self._preview_conflicts)}개"
            if self._preview_conflicts else ""
        )
        reply = QMessageBox.question(
            self,
            "파일 정리 최종 확인",
            f"다음 정리를 시작합니다.\n\n"
            f"이동될 파일: {len(move_plan)}개\n"
            f"대상 폴더: {self._preview_base_path}"
            f"{conflict_note}\n\n"
            f"파일을 이동하시겠습니까?\n"
            f"(취소 시 파일 변경 없음)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return  # 취소 → 파일 변경 0건

        # Plan 시점 fingerprint (변경 감지용)
        plan_fingerprints = self._build_plan_fingerprints()

        from src.utils.workers import OrganizeApplyWorker
        from src.ui.widgets.progress_dialog import TaskProgressDialog

        self._grouped_screen.set_confirm_enabled(False)
        self._table_screen.auto_btn.setEnabled(False)

        self._apply_dialog = TaskProgressDialog(
            "파일 정리 중",
            "파일을 이동하고 있습니다...",
            parent=self,
            unit="파일",
        )

        db_path = getattr(self.core, "db_path", "file_manager.db")
        self._apply_worker = OrganizeApplyWorker(
            move_plan=move_plan,
            db_path=db_path,
            plan_fingerprints=plan_fingerprints,
        )
        self._apply_worker.progress.connect(self._on_apply_progress)
        self._apply_worker.completed.connect(self._on_apply_completed)
        self._apply_worker.error.connect(self._on_apply_error)
        self._apply_worker.start()
        self._apply_dialog.show()

    def _clear_pending_preview(self):
        """이전 Preview의 목적지/이동 계획을 폐기한다. 파일과 DB는 변경하지 않는다."""
        self._preview_base_path = ""
        self._preview_move_plan = []
        self._preview_conflicts = []
        self._grouped_screen.set_confirm_enabled(False)

    def _build_plan_fingerprints(self) -> dict:
        """최근 분석 Plan에서 파일 stat 정보를 추출한다 (변경 감지용)."""
        result = {}
        for list_key in ("already_analyzed", "new", "changed", "same_content", "incomplete", "pending"):
            for item in self._last_plan.get(list_key, []):
                path = item.get("file_path", "")
                if path:
                    norm = os.path.normcase(os.path.abspath(path))
                    result[norm] = {
                        "hash": item.get("file_hash", ""),
                        "size": item.get("file_size"),
                        "mtime_ns": item.get("file_mtime_ns"),
                    }
        return result

    def _on_apply_progress(self, current, total, detail):
        if self._apply_dialog:
            self._apply_dialog.update_progress(current, total, detail)

    def _close_apply_dialog(self):
        if self._apply_dialog:
            self._apply_dialog.close()
            self._apply_dialog = None
        self._table_screen.auto_btn.setEnabled(True)

    def _on_apply_completed(self, result):
        self._close_apply_dialog()
        moved = result.get("moved", [])
        failed = result.get("failed", [])
        rollback_failures = result.get("partial_rollback_failures", [])
        index_sync_errors = result.get("index_sync_errors", [])
        history_errors = result.get("history_errors", [])

        if rollback_failures:
            msg = (
                f"파일 정리 중 오류가 발생했으며 일부 롤백이 실패했습니다.\n"
                f"이동 성공: {len(moved)}개\n"
                f"롤백 실패: {len(rollback_failures)}개\n\n"
                "영향 받은 파일 위치를 직접 확인해 주세요:\n"
                + "\n".join(rollback_failures[:5])
            )
            if index_sync_errors:
                msg += f"\n\n색인 동기화 오류 {len(index_sync_errors)}건 (다음 분석 시 자동 복구됩니다)"
            QMessageBox.critical(self, "정리 오류 - 수동 확인 필요", msg)
        elif failed:
            rolled_back = result.get("rolled_back", [])
            rb_ok = sum(1 for r in rolled_back if r.get("success"))
            if moved or rb_ok:
                msg = (
                    f"일부 파일 정리에 실패하여 롤백했습니다.\n"
                    f"롤백 성공: {rb_ok}개\n"
                    f"실패: {len(failed)}개\n\n"
                    "실패 사유:\n"
                    + "\n".join(f["reason"] for f in failed[:5])
                )
            else:
                msg = (
                    f"파일 정리에 실패했습니다.\n실패: {len(failed)}개\n\n"
                    + "\n".join(f["reason"] for f in failed[:5])
                )
            QMessageBox.warning(self, "파일 정리 실패", msg)
            # 실패 후 confirm_btn 복구
            self._grouped_screen.set_confirm_enabled(True)
        else:
            # 전체 성공
            self._last_plan_files = []
            self._last_plan = {}
            self._clear_pending_preview()
            self._load_files_from_db()
            self._refresh_database_views()
            msg = f"파일 정리가 완료되었습니다.\n이동: {len(moved)}개"
            if index_sync_errors:
                msg += (
                    f"\n\n색인 동기화 오류 {len(index_sync_errors)}건 발생:\n"
                    + "\n".join(index_sync_errors[:3])
                    + "\n(다음 '자동정리' 실행 시 자동 복구됩니다)"
                )
            if history_errors:
                msg += (
                    f"\n\n이력 기록 실패: Undo를 사용할 수 없습니다.\n"
                    + "\n".join(history_errors[:2])
                )
            QMessageBox.information(self, "파일 정리 완료", msg)
            self._show_table()

    def _on_apply_error(self, message):
        self._close_apply_dialog()
        self._grouped_screen.set_confirm_enabled(True)
        safe_msg = message.split("\n")[0][:300] if message else "알 수 없는 오류"
        QMessageBox.critical(self, "파일 정리 오류", safe_msg)

    # ── Batch 10: 미분류 파일 처리 ───────────────────────────────────────────

    def _get_untagged_from_plan(self) -> list:
        """마지막 분석 Plan에서 AI 태그가 없는 파일 목록을 반환한다.

        DB에 등록됐지만 tags가 없거나, DB에 아직 없는 파일도 포함된다.
        실제 파일이 존재하는 것만 반환한다.
        """
        if not self._last_plan_files:
            return []
        try:
            if self.core:
                tagged = self.core.get_files_for_organize()
                tagged_paths = {
                    os.path.normcase(os.path.abspath(f["file_path"]))
                    for f in tagged
                }
            else:
                tagged_paths = set()
        except Exception:
            tagged_paths = set()

        untagged = []
        for p in self._last_plan_files:
            norm = os.path.normcase(os.path.abspath(p))
            if norm not in tagged_paths and os.path.isfile(p):
                untagged.append(p)
        return untagged

    @staticmethod
    def _get_file_kind_by_extension(file_path: str) -> str:
        """확장자 기반 파일 유형을 반환한다 (AI 분류 아님, 표시 목적만)."""
        ext = Path(file_path).suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif"}:
            return "image"
        if ext in {".txt", ".doc", ".docx", ".pdf", ".hwp", ".ppt", ".pptx",
                   ".xlsx", ".xls", ".csv"}:
            return "doc"
        return "default"

    @staticmethod
    def _check_ai_available() -> bool:
        """AI 서버가 응답 가능한지 빠르게 확인한다 (최대 3초, UI 호출 전 사용)."""
        try:
            import requests
            from src.ai.config import AIConfig
            cfg = AIConfig()
            url = f"http://{cfg.llama_host}:{cfg.llama_port}/health"
            resp = requests.get(url, timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def _start_untagged_analysis(self, paths: list):
        """미분류 파일에 대해 background AI 분석을 시작한다.

        - 이미 실행 중이면 중복 실행하지 않는다.
        - 이번 세션에 이미 시도한 파일은 제외한다 (무한 재분석 방지).
        - AI 결과는 FolderScanAndTagWorker가 DB에 저장한다.
        """
        if self._untagged_worker and self._untagged_worker.isRunning():
            QMessageBox.information(self, "AI 분석", "이미 AI 분석 작업이 진행 중입니다.")
            return
        if not self.core:
            return

        # 중복/무한 분석 방지 — 이미 시도한 경로 제외
        new_paths = [
            p for p in paths
            if os.path.normcase(os.path.abspath(p)) not in self._analysis_attempted
        ]
        if not new_paths:
            QMessageBox.information(
                self, "AI 분석",
                "이번 세션에서 해당 파일은 이미 분석을 시도했습니다.\n"
                "재분석이 필요하면 '자동정리'를 다시 실행해 주세요.",
            )
            return

        # 시도 기록 (이번 세션에서 재분석 방지)
        for p in new_paths:
            self._analysis_attempted.add(os.path.normcase(os.path.abspath(p)))

        # 현재 Plan context 기록 (분석 완료 시 stale 여부 판단용)
        self._analysis_plan_context_id = self._plan_context_id

        from src.utils.workers import FolderScanAndTagWorker
        from src.ui.widgets.progress_dialog import TaskProgressDialog

        self._untagged_dialog = TaskProgressDialog(
            "AI 분석 중",
            "AI 태그를 생성하고 있습니다...",
            parent=self,
            unit="파일",
        )

        self._untagged_worker = FolderScanAndTagWorker(new_paths, self.core)
        self._untagged_worker.progress.connect(
            lambda msg: self._untagged_dialog.setLabelText(msg) if self._untagged_dialog else None
        )
        self._untagged_worker.fileProgress.connect(
            lambda c, t, name: (
                self._untagged_dialog.update_progress(c, t, name)
                if self._untagged_dialog else None
            )
        )
        self._untagged_worker.finished.connect(self._on_untagged_analysis_finished)
        self._untagged_worker.error.connect(self._on_untagged_analysis_error)
        self._untagged_worker.start()
        self._untagged_dialog.show()

    def _close_untagged_dialog(self):
        if self._untagged_dialog:
            self._untagged_dialog.close()
            self._untagged_dialog = None

    def _on_untagged_analysis_finished(self, summary: dict):
        """AI background 분석 완료 처리."""
        self._close_untagged_dialog()
        summary = summary or {}
        success_count = summary.get("success", 0)
        failed_list = summary.get("failed", [])

        if self._apply_worker and self._apply_worker.isRunning():
            return
        if self._undo_worker and self._undo_worker.isRunning():
            return

        if self._analysis_plan_context_id != self._plan_context_id:
            return

        if success_count > 0:
            # DB에서 최신 태그를 읽어 Preview 전체를 재구성한다
            self._rebuild_preview_from_db(newly_tagged=success_count)
        else:
            untagged_remaining = self._get_untagged_from_plan()
            self._last_untagged_files = untagged_remaining
            # 실패 원인을 추출해 배너에 표시
            reasons = list({
                f["reason"].split("\n")[0][:80]
                for f in failed_list if f.get("reason")
            })
            reason_text = reasons[0] if reasons else "원인 불명"
            banner_text = (
                f"AI 분석 실패 ({len(failed_list)}개) — {reason_text} | "
                "수동 태그 지정으로 직접 분류할 수 있습니다."
            )
            self._grouped_screen.set_banner_text(banner_text)

    def _on_untagged_analysis_error(self, message: str):
        self._close_untagged_dialog()
        safe_msg = message.split("\n")[0][:200] if message else "알 수 없는 오류"
        QMessageBox.warning(self, "AI 분석 오류", f"AI 분석 중 오류가 발생했습니다:\n{safe_msg}")

    def _manual_tag_unclassified(self):
        """미분류 파일을 다중 선택해 태그별로 나눠 분류하는 다이얼로그를 연다."""
        if not self._last_untagged_files:
            QMessageBox.information(self, "수동 태그 지정", "미분류 파일이 없습니다.")
            return

        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
            QLineEdit, QPushButton, QLabel, QAbstractItemView,
        )

        _DLG_QSS = """
            QDialog { background: #FFFFFF; }
            QLabel { color: #2D2D3A; font-size: 13px; }
            QLabel#dlgTitle { font-size: 16px; font-weight: 700; }
            QLabel#dlgDesc  { font-size: 12px; color: #8A8CA5; }
            QLabel#logLabel { font-size: 12px; color: #6C5CE7; }
            QListWidget {
                background: #F9F9FC; border: 1px solid #E4E6EF;
                border-radius: 8px; font-size: 13px; outline: 0;
            }
            QListWidget::item { padding: 6px 10px; border-radius: 4px; }
            QListWidget::item:selected { background: #EFEBFF; color: #2D2D3A; }
            QListWidget::item:hover { background: #F5F0FF; }
            QLineEdit {
                background: #F5F6FA; border: 1px solid #E4E6EF;
                border-radius: 8px; padding: 0 12px; font-size: 13px; color: #2D2D3A;
            }
            QLineEdit:focus { border: 1px solid #6C5CE7; background: #FFFFFF; }
            QPushButton#applyBtn {
                background: #6C5CE7; color: white; border: none;
                border-radius: 8px; font-weight: 600; font-size: 13px;
            }
            QPushButton#applyBtn:hover { background: #5A4BD1; }
            QPushButton#okBtn {
                background: #6C5CE7; color: white; border: none;
                border-radius: 8px; padding: 8px 24px; font-weight: 600; font-size: 13px;
            }
            QPushButton#okBtn:hover { background: #5A4BD1; }
            QPushButton#cancelBtn {
                background: #F5F6FA; color: #2D2D3A; border: 1px solid #E4E6EF;
                border-radius: 8px; padding: 8px 24px; font-size: 13px; font-weight: 600;
            }
            QPushButton#cancelBtn:hover { background: #EFEBFF; color: #6C5CE7; }
        """

        dlg = QDialog(self)
        dlg.setWindowTitle("수동 태그 지정")
        dlg.setMinimumSize(620, 520)
        dlg.setStyleSheet(_DLG_QSS)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title_lbl = QLabel("수동 태그 지정")
        title_lbl.setObjectName("dlgTitle")
        root.addWidget(title_lbl)

        hint = QLabel(
            "파일을 선택(Ctrl/Shift로 다중 선택)한 뒤 태그를 입력하고 '적용'을 누르세요.\n"
            "태그 이름으로 정리 폴더가 생성됩니다. 다른 태그로 반복 적용할 수 있습니다."
        )
        hint.setObjectName("dlgDesc")
        hint.setWordWrap(True)
        root.addWidget(hint)

        file_list = QListWidget()
        file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        file_list.setAlternatingRowColors(True)

        # DB에서 정확한 경로와 ID를 읽어 목록 구성 (경로 불일치 방지)
        import sqlite3 as _sqlite3
        db_path = getattr(self.core, "db_path", "file_manager.db")
        norm_to_db: dict[str, tuple[int, str]] = {}  # normcase(path) → (id, db_path)
        try:
            _conn = _sqlite3.connect(db_path, timeout=10)
            for scan_path in self._last_untagged_files:
                norm = os.path.normcase(os.path.abspath(scan_path))
                row = _conn.execute(
                    "SELECT id, file_path FROM files WHERE file_path = ?", (scan_path,)
                ).fetchone()
                if not row:
                    # 경로가 다소 다를 수 있으므로 normcase로 한 번 더 탐색
                    like = _conn.execute(
                        "SELECT id, file_path FROM files WHERE LOWER(file_path) = ?",
                        (scan_path.lower(),)
                    ).fetchone()
                    row = like
                if row:
                    norm_to_db[norm] = (row[0], row[1])
            _conn.close()
        except Exception:
            pass

        for p in self._last_untagged_files:
            norm = os.path.normcase(os.path.abspath(p))
            db_entry = norm_to_db.get(norm)
            label = Path(p).name
            item = QListWidgetItem(label)
            # UserRole: (file_id, exact_db_path) — 없으면 fallback으로 스캔 경로
            item.setData(Qt.UserRole, db_entry if db_entry else (None, p))
            item.setToolTip(p)
            file_list.addItem(item)
        root.addWidget(file_list, stretch=1)

        # 파일 열기 / 삭제 액션 행
        action_row = QHBoxLayout()
        open_btn = QPushButton("파일 열기")
        open_btn.setObjectName("cancelBtn")
        open_btn.setFixedHeight(32)
        open_btn.setToolTip("선택한 파일을 기본 앱으로 엽니다 (더블클릭도 가능)")
        delete_btn = QPushButton("선택 파일 삭제")
        delete_btn.setFixedHeight(32)
        delete_btn.setStyleSheet(
            "QPushButton { background:#EF4444; color:white; border:none; "
            "border-radius:8px; font-weight:600; font-size:13px; }"
            "QPushButton:hover { background:#DC2626; }"
        )
        action_row.addWidget(open_btn)
        action_row.addWidget(delete_btn)
        action_row.addStretch()
        root.addLayout(action_row)

        def _open_selected():
            selected = file_list.selectedItems()
            if not selected:
                return
            _, path = selected[0].data(Qt.UserRole)
            try:
                os.startfile(path)
            except Exception as e:
                QMessageBox.warning(dlg, "파일 열기 실패", str(e))

        def _delete_selected():
            selected = file_list.selectedItems()
            if not selected:
                QMessageBox.warning(dlg, "선택 필요", "삭제할 파일을 선택해 주세요.")
                return
            names = "\n".join(f"  • {item.text().split('  →')[0]}" for item in selected[:5])
            if len(selected) > 5:
                names += f"\n  ... 외 {len(selected)-5}개"
            reply = QMessageBox.question(
                dlg, "파일 삭제 확인",
                f"선택한 {len(selected)}개 파일을 삭제하시겠습니까?\n\n{names}\n\n"
                "이 작업은 되돌릴 수 없습니다.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            import time as _t
            conn2 = _sqlite3.connect(db_path, timeout=10)
            del_failed = []
            for item in selected:
                fid, path = item.data(Qt.UserRole)
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                    if fid is not None:
                        conn2.execute("DELETE FROM files WHERE id=?", (fid,))
                    else:
                        conn2.execute("DELETE FROM files WHERE file_path=?", (path,))
                    # 인덱스 정리
                    for tbl in ("file_text_index", "file_fingerprint_cache"):
                        conn2.execute(f"DELETE FROM {tbl} WHERE file_path=?", (path,))
                    # _last_untagged_files에서 제거
                    self._last_untagged_files = [
                        p for p in self._last_untagged_files if p != path
                    ]
                    row = file_list.row(item)
                    file_list.takeItem(row)
                    if path in applied:
                        del applied[path]
                except Exception as e:
                    del_failed.append(f"{Path(path).name}: {e}")
            conn2.commit()
            conn2.close()
            if del_failed:
                QMessageBox.warning(dlg, "일부 삭제 실패", "\n".join(del_failed[:5]))

        open_btn.clicked.connect(_open_selected)
        file_list.itemDoubleClicked.connect(lambda item: _open_selected())
        delete_btn.clicked.connect(_delete_selected)

        # 태그 입력 + 적용 행
        tag_row = QHBoxLayout()
        tag_row.setSpacing(8)
        tag_input = QLineEdit()
        tag_input.setPlaceholderText("태그 입력 (예: 업무, 개인, 프로젝트)")
        tag_input.setFixedHeight(36)
        apply_btn = QPushButton("선택 파일에 적용")
        apply_btn.setObjectName("applyBtn")
        apply_btn.setFixedHeight(36)
        apply_btn.setFixedWidth(150)
        tag_row.addWidget(tag_input, stretch=1)
        tag_row.addWidget(apply_btn)
        root.addLayout(tag_row)

        # 적용 이력 표시
        self._manual_tag_log = QLabel("")
        self._manual_tag_log.setObjectName("logLabel")
        self._manual_tag_log.setWordWrap(True)
        root.addWidget(self._manual_tag_log)

        # applied: db_path → (file_id_or_None, tag)
        applied: dict[str, tuple] = {}

        def _apply():
            tag = tag_input.text().strip()
            if not tag:
                QMessageBox.warning(dlg, "태그 입력 필요", "태그를 입력해 주세요.")
                return
            selected = file_list.selectedItems()
            if not selected:
                QMessageBox.warning(dlg, "파일 선택 필요", "파일을 선택해 주세요.")
                return
            for item in selected:
                fid, db_path = item.data(Qt.UserRole)
                applied[db_path] = (fid, tag)
                item.setText(f"{Path(db_path).name}  →  {tag}")
                item.setForeground(Qt.darkGreen)
            log_lines = {}
            for dp, (_, t) in applied.items():
                log_lines.setdefault(t, []).append(Path(dp).name)
            self._manual_tag_log.setText(
                "  |  ".join(f"[{t}] {len(fs)}개" for t, fs in log_lines.items())
            )
            tag_input.clear()

        apply_btn.clicked.connect(_apply)
        tag_input.returnPressed.connect(_apply)

        # 확인/취소
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("취소")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn = QPushButton("정리 목록에 반영")
        ok_btn.setObjectName("okBtn")
        ok_btn.setFixedHeight(36)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

        if dlg.exec() != QDialog.Accepted or not applied:
            return

        # DB 업데이트 — ID가 있으면 ID 기반, 없으면 경로 기반
        import time as _time
        now = _time.strftime("%Y-%m-%d %H:%M:%S")
        failed_paths = []
        try:
            conn = _sqlite3.connect(db_path, timeout=10)
            for db_path_key, (fid, tag) in applied.items():
                if fid is not None:
                    cur = conn.execute(
                        "UPDATE files SET tags=?, category=?, updated_at=? WHERE id=?",
                        (tag, f"#{tag}", now, fid),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE files SET tags=?, category=?, updated_at=? WHERE file_path=?",
                        (tag, f"#{tag}", now, db_path_key),
                    )
                if cur.rowcount == 0:
                    failed_paths.append(Path(db_path_key).name)
            conn.commit()
            conn.close()
        except Exception as e:
            QMessageBox.critical(self, "수동 태그 지정 오류", str(e))
            return
        if failed_paths:
            QMessageBox.warning(
                self, "일부 반영 실패",
                f"다음 파일은 DB에서 찾을 수 없어 태그를 저장하지 못했습니다:\n"
                + "\n".join(failed_paths[:10])
            )

        self._rebuild_preview_from_db(newly_tagged=len(applied) - len(failed_paths),
                                      ignore_plan_filter=True)

    def _rebuild_preview_from_db(self, newly_tagged: int = 0, ignore_plan_filter: bool = False):
        """AI 태깅 완료 후 DB에서 최신 태그를 읽어 Preview를 완전히 재구성한다.

        ignore_plan_filter=True: 수동 태그 직후처럼 plan_file_set과 무관하게
        미분류 목록 파일 기준으로 재구성한다.
        """
        if not self.core or not self._preview_base_path:
            return

        plan_file_set = {
            os.path.normcase(os.path.abspath(p))
            for p in self._last_plan_files
        }

        # DB에서 최신 태그 조회
        all_tagged = self.core.get_files_for_organize()
        if ignore_plan_filter or not plan_file_set:
            # plan_file_set을 신뢰할 수 없는 경우 미분류 목록 + 태그된 파일 전체 사용
            untagged_norm = {
                os.path.normcase(os.path.abspath(p))
                for p in self._last_untagged_files
            }
            organize_files = [
                fi for fi in all_tagged
                if os.path.normcase(os.path.abspath(fi["file_path"])) in untagged_norm
                or os.path.normcase(os.path.abspath(fi["file_path"])) in plan_file_set
            ]
        else:
            organize_files = [
                fi for fi in all_tagged
                if os.path.normcase(os.path.abspath(fi["file_path"])) in plan_file_set
            ]

        # 미분류 목록 갱신
        tagged_norm = {
            os.path.normcase(os.path.abspath(fi["file_path"])) for fi in organize_files
        }
        if self._last_plan_files:
            self._last_untagged_files = [
                p for p in self._last_plan_files
                if os.path.normcase(os.path.abspath(p)) not in tagged_norm
                and os.path.isfile(p)
            ]
        else:
            # plan이 없는 경우(수동 태그 직후 등) — 기존 미분류에서 방금 태깅된 파일만 제거
            self._last_untagged_files = [
                p for p in self._last_untagged_files
                if os.path.normcase(os.path.abspath(p)) not in tagged_norm
                and os.path.isfile(p)
            ]

        grouped_files = self.core.group_files_by_tags(organize_files)
        preview = self.core.build_organize_preview(grouped_files, self._preview_base_path)

        files_by_path = {
            os.path.normcase(os.path.abspath(fi["file_path"])): fi
            for fi in organize_files
        }
        groups_by_tag: dict = {}
        move_plan = []
        conflicts = []
        for item in preview:
            label = item["file_name"]
            if item["has_conflict"]:
                label = f"{label} (충돌로 제외)"
                conflicts.append(item)
            else:
                file_entry = files_by_path.get(
                    os.path.normcase(os.path.abspath(item["source_path"]))
                )
                if file_entry:
                    move_plan.append({
                        "file_id": file_entry["id"],
                        "file_path": item["source_path"],
                        "target_path": item["target_path"],
                        "file_name": item["file_name"],
                    })
            groups_by_tag.setdefault(item["tag"], []).append((
                self._get_file_kind_by_extension(item["source_path"]),
                label,
            ))

        self._preview_move_plan = move_plan
        self._preview_conflicts = conflicts

        groups_ui = [(tag, files[:10]) for tag, files in groups_by_tag.items()]
        if self._last_untagged_files:
            untagged_cards = [
                (
                    self._get_file_kind_by_extension(f),
                    (Path(f).name[:15] + "...") if len(Path(f).name) > 15 else Path(f).name,
                )
                for f in self._last_untagged_files[:10]
            ]
            groups_ui.append((
                f"미분류 (AI 태그 없음) — {len(self._last_untagged_files)}개",
                untagged_cards,
            ))

        if not groups_ui:
            groups_ui = [("분석 완료 (스캔된 파일 없음)", [])]

        untagged_count = len(self._last_untagged_files)
        banner_text = (
            f"AI 분석 완료 | 새로 분류됨: {newly_tagged}개"
            + (f" | 미분류: {untagged_count}개" if untagged_count else "")
            + " | 정리 계획이 갱신되었습니다. 파일은 아직 변경되지 않습니다."
        )
        self._grouped_screen.set_banner_text(banner_text)
        self._grouped_screen.set_groups(groups_ui)
        self._grouped_screen.set_confirm_enabled(bool(move_plan))

    # ── Batch 13: History / Undo ──────────────────────────────────────────────

    def _show_history_dialog(self):
        """정리 이력 다이얼로그를 열어 Undo 옵션을 제공한다."""
        if not self.core:
            QMessageBox.information(self, "정리 이력", "코어 시스템이 초기화되지 않았습니다.")
            return

        db_path = getattr(self.core, "db_path", "file_manager.db")
        operations = self._load_history_operations(db_path)

        if not operations:
            QMessageBox.information(self, "정리 이력", "정리 이력이 없습니다.")
            return

        dlg = _HistoryDialog(operations, self)
        dlg.undoRequested.connect(self._confirm_and_start_undo)
        dlg.exec()

    def _load_history_operations(self, db_path: str) -> list:
        """operation 단위 정리 이력을 최신 순으로 반환한다."""
        import sqlite3 as _sqlite3
        try:
            conn = _sqlite3.connect(db_path, timeout=5)
            rows = conn.execute(
                """
                SELECT operation_id,
                       MIN(applied_at) as applied_at,
                       COUNT(*) as file_count,
                       COUNT(CASE WHEN status = 'applied' THEN 1 END) as applied_count
                FROM organize_history
                GROUP BY operation_id
                ORDER BY MIN(applied_at) DESC
                LIMIT 20
                """
            ).fetchall()
            conn.close()
            return [
                {
                    "operation_id": r[0],
                    "applied_at": r[1],
                    "file_count": r[2],
                    "applied_count": r[3],
                }
                for r in rows
            ]
        except Exception:
            return []

    def _load_undo_records(self, db_path: str, operation_id: str) -> list:
        """Undo 대상 records를 반환한다 (status='applied'만)."""
        import sqlite3 as _sqlite3
        try:
            conn = _sqlite3.connect(db_path, timeout=5)
            rows = conn.execute(
                "SELECT id, operation_id, original_path, moved_path, file_hash, file_size, status "
                "FROM organize_history WHERE operation_id = ? AND status = 'applied'",
                (operation_id,),
            ).fetchall()
            conn.close()
            return [
                {
                    "id": r[0], "operation_id": r[1], "original_path": r[2],
                    "moved_path": r[3], "file_hash": r[4], "file_size": r[5], "status": r[6],
                }
                for r in rows
            ]
        except Exception:
            return []

    def _confirm_and_start_undo(self, operation_id: str):
        """Undo 사용자 확인 후 Worker를 시작한다."""
        if not self.core:
            return

        if self._undo_worker and self._undo_worker.isRunning():
            QMessageBox.information(self, "되돌리기", "이미 되돌리기 작업이 진행 중입니다.")
            return

        if self._apply_worker and self._apply_worker.isRunning():
            QMessageBox.information(self, "되돌리기", "파일 정리 작업 중에는 되돌리기를 실행할 수 없습니다.")
            return

        db_path = getattr(self.core, "db_path", "file_manager.db")
        records = self._load_undo_records(db_path, operation_id)

        if not records:
            QMessageBox.information(self, "되돌리기", "되돌릴 항목을 찾을 수 없습니다.")
            return

        reply = QMessageBox.question(
            self, "되돌리기 확인",
            f"이 정리 작업으로 이동한 {len(records)}개 파일을 원래 위치로 되돌립니다.\n"
            "계속하시겠습니까?\n(취소 시 파일 변경 없음)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from src.utils.workers import OrganizeUndoWorker
        from src.ui.widgets.progress_dialog import TaskProgressDialog

        self._table_screen.auto_btn.setEnabled(False)

        self._undo_dialog = TaskProgressDialog(
            "되돌리는 중",
            "파일을 원래 위치로 이동하고 있습니다...",
            parent=self,
            unit="파일",
        )

        self._undo_worker = OrganizeUndoWorker(records, db_path)
        self._undo_worker.progress.connect(self._on_undo_progress)
        self._undo_worker.completed.connect(self._on_undo_completed)
        self._undo_worker.error.connect(self._on_undo_error)
        self._undo_worker.start()
        self._undo_dialog.show()

    def _on_undo_progress(self, current, total, detail):
        if self._undo_dialog:
            self._undo_dialog.update_progress(current, total, detail)

    def _close_undo_dialog(self):
        if self._undo_dialog:
            self._undo_dialog.close()
            self._undo_dialog = None
        self._table_screen.auto_btn.setEnabled(True)

    def _on_undo_completed(self, result):
        self._close_undo_dialog()
        undone = result.get("undone", [])
        failed = result.get("failed", [])
        rollback_failures = result.get("partial_rollback_failures", [])
        index_sync_errors = result.get("index_sync_errors", [])

        if rollback_failures:
            msg = (
                f"되돌리기 중 오류가 발생했으며 일부 rollback이 실패했습니다.\n"
                f"되돌림 성공: {len(undone)}개 | Rollback 실패: {len(rollback_failures)}개\n\n"
                "영향 받은 파일 위치를 직접 확인해 주세요:\n"
                + "\n".join(rollback_failures[:5])
            )
            QMessageBox.critical(self, "되돌리기 오류 - 수동 확인 필요", msg)
        elif failed:
            rolled = result.get("rolled_back", [])
            rb_ok = sum(1 for r in rolled if r.get("success"))
            msg = (
                f"일부 파일 되돌리기에 실패했습니다.\n"
                f"성공: {len(undone)}개 | 실패: {len(failed)}개 | Rollback 성공: {rb_ok}개\n\n"
                + "\n".join(f["reason"] for f in failed[:5])
            )
            QMessageBox.warning(self, "되돌리기 부분 실패", msg)
        else:
            msg = f"{len(undone)}개 파일을 원래 위치로 되돌렸습니다."
            if index_sync_errors:
                msg += f"\n\n색인 동기화 오류 {len(index_sync_errors)}건 (다음 '자동정리'에서 자동 복구됩니다)"
            self._load_files_from_db()
            self._refresh_database_views()
            self._show_table()
            QMessageBox.information(self, "되돌리기 완료", msg)

    def _on_undo_error(self, message):
        self._close_undo_dialog()
        safe_msg = message.split("\n")[0][:300] if message else "알 수 없는 오류"
        QMessageBox.critical(self, "되돌리기 오류", safe_msg)

    def set_file_rows(self, rows):
        self._table_screen.set_rows(rows)

    def _refresh_database_views(self):
        if self.refresh_manager:
            self.refresh_manager.refresh()

