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
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QStackedWidget, QScrollArea, QAbstractItemView, QFileDialog,
    QMessageBox, QProgressDialog, QInputDialog, QDialog,
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
    """최근 파일 정리 이력을 표시하고 Undo를 제공하는 최소 다이얼로그."""

    undoRequested = Signal(str)  # operation_id

    def __init__(self, operations: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("정리 이력")
        self.setMinimumWidth(640)
        self.setMinimumHeight(340)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel("최근 파일 정리 이력입니다. Undo로 이동된 파일을 원위치로 되돌릴 수 있습니다.")
        info.setWordWrap(True)
        layout.addWidget(info)

        table = QTableWidget(len(operations), 4)
        table.setHorizontalHeaderLabels(["날짜/시간", "이동 파일", "상태", "동작"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)

        for row, op in enumerate(operations):
            table.setItem(row, 0, QTableWidgetItem((op["applied_at"] or "")[:19]))
            table.setItem(row, 1, QTableWidgetItem(f"{op['file_count']}개"))
            can_undo = (op["applied_count"] or 0) > 0
            table.setItem(row, 2, QTableWidgetItem("정리 완료" if can_undo else "되돌림 완료"))
            if can_undo:
                btn = QPushButton("Undo")
                btn.setCursor(Qt.PointingHandCursor)
                oid = op["operation_id"]
                btn.clicked.connect(lambda checked, o=oid: self._request_undo(o))
                table.setCellWidget(row, 3, btn)
            else:
                table.setItem(row, 3, QTableWidgetItem("—"))

        layout.addWidget(table, stretch=1)

        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

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
            self.group_layout.insertWidget(self.group_layout.count() - 1, _GroupedFolderCard(name, files))

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
        self._plan_worker = None
        self._plan_dialog = None
        self._apply_worker = None
        self._apply_dialog = None
        self._last_plan: dict = {}
        self._last_plan_files: list = []
        self._last_untagged_files: list = []
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
        if self.core:
            self._load_files_from_db()

    def _show_grouped(self):
        self._inner_stack.setCurrentWidget(self._grouped_screen)

    def _show_table(self):
        self._inner_stack.setCurrentWidget(self._table_screen)

    def _load_files_from_db(self):
        try:
            rows = [
                (file_info["file_name"], ", ".join(file_info["tags"]), file_info["file_path"])
                for file_info in self.core.get_files_for_organize()
            ]
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
        """경로 추가 처리 (파일 스캔 및 테이블 업데이트)"""
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
 
            QMessageBox.information(
                self, "경로 추가됨",
                f"경로가 추가되고 {len(scanned_files)}개 파일이 로드되었습니다:\n{path}\n\n"
                "AI 태깅은 저장목록의 '미태깅 전체 AI 태깅'에서 선택하여 실행할 수 있습니다.",
            )
 
        except Exception as e:
            QMessageBox.critical(self, "오류", f"파일 스캔 중 오류가 발생했습니다:\n{str(e)}")
 
    def _start_ai_tagging(self, paths):
        if self._tagging_worker and self._tagging_worker.isRunning():
            QMessageBox.information(self, "AI 태깅", "이미 태깅 작업이 진행 중입니다.")
            return
        from src.utils.workers import FolderScanAndTagWorker
        self._tagging_worker = FolderScanAndTagWorker(paths, self.core)

        self._tagging_dialog = QProgressDialog(
            "AI 태깅을 준비하고 있습니다...", None, 0, 0, self
        )
        self._tagging_dialog.setWindowTitle("AI 태깅 중")
        self._tagging_dialog.setWindowModality(Qt.WindowModal)
        self._tagging_dialog.setMinimumDuration(0)
        self._tagging_dialog.setAutoClose(False)
        self._tagging_dialog.setAutoReset(False)

        self._tagging_worker.progress.connect(self._tagging_dialog.setLabelText)
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
        QMessageBox.information(self, "AI 태깅", f"AI 태깅 완료: 성공 {summary.get('success', 0)}개, 실패 {len(summary.get('failed', []))}개")

    def _on_tagging_error(self, message):
        if self._tagging_dialog is not None:
            self._tagging_dialog.close()
            self._tagging_dialog = None
        QMessageBox.critical(self, "AI 태깅 오류", message)

    def _on_auto_organize(self):
        if not self.core:
            QMessageBox.warning(self, "자동 정리", "코어 시스템이 초기화되지 않았습니다.")
            return

        # 중복 실행 방지
        if self._plan_worker and self._plan_worker.isRunning():
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

        from src.utils.workers import FolderAnalysisPlanWorker
        from src.ui.widgets.progress_dialog import TaskProgressDialog

        self._table_screen.auto_btn.setEnabled(False)

        self._plan_dialog = TaskProgressDialog(
            "폴더 분석 중",
            "폴더를 분석하여 정리 계획을 생성합니다. 파일은 아직 변경되지 않습니다.",
            parent=self,
            unit="파일",
        )

        db_path = getattr(self.core, 'db_path', 'file_manager.db')
        self._plan_worker = FolderAnalysisPlanWorker(folders, db_path=db_path)
        self._plan_worker.progress.connect(self._on_plan_progress)
        self._plan_worker.completed.connect(self._on_plan_completed)
        self._plan_worker.error.connect(self._on_plan_error)
        self._plan_worker.start()
        self._plan_dialog.show()

    def _get_target_folders(self):
        """현재 테이블의 파일 경로에서 존재하고 접근 가능한 부모 폴더 목록을 반환한다."""
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

        # 스캔된 파일을 폴더별로 그룹화하여 카드로 표시
        scanned_files = plan.get("scanned", [])
        folder_files: dict = {}
        for f in scanned_files:
            folder = str(Path(f).parent)
            ext = Path(f).suffix.lower()
            if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
                kind = "image"
            elif ext in {".txt", ".doc", ".docx", ".pdf", ".hwp", ".ppt", ".pptx"}:
                kind = "doc"
            else:
                kind = "default"
            name = Path(f).name
            label = name[:15] + "..." if len(name) > 15 else name
            folder_files.setdefault(folder, []).append((kind, label))

        groups_ui = []
        for folder_path, files in folder_files.items():
            folder_name = Path(folder_path).name or folder_path
            groups_ui.append((folder_name, files[:10]))

        if not groups_ui:
            groups_ui = [("분석 완료 (스캔된 파일 없음)", [])]

        self._last_plan = plan
        self._last_plan_files = plan.get("scanned", [])

        # Batch 10: 미분류 파일(AI 태그 없음) 감지 및 표시
        untagged = self._get_untagged_from_plan()
        self._last_untagged_files = untagged
        tagged_count = len(self._last_plan_files) - len(untagged)

        if untagged:
            banner_text = (
                f"분석 완료 — {scanned_count:,}개 파일 스캔 | "
                f"태그 있음: {tagged_count:,}개 | "
                f"미분류(AI 태그 없음): {len(untagged):,}개 | "
                "파일은 아직 변경되지 않습니다."
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
                "파일은 아직 변경되지 않습니다."
            )

        self._grouped_screen.set_banner_text(banner_text)
        self._grouped_screen.set_groups(groups_ui)  # noqa: F821 (banner_text always set above)
        # Batch 9: Plan이 준비되면 이대로 정리하기 버튼 활성화
        self._grouped_screen.set_confirm_enabled(True)
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

        # 분석 Plan에서 대상 파일 확보 + DB tags 조회
        plan_file_set = {
            os.path.normcase(os.path.abspath(p))
            for p in self._last_plan_files
        }
        all_tagged = self.core.get_files_for_organize()
        organize_files = (
            [f for f in all_tagged
             if os.path.normcase(os.path.abspath(f["file_path"])) in plan_file_set]
            if plan_file_set else all_tagged
        )

        grouped_files = self.core.group_files_by_tags(organize_files)
        untagged = self._get_untagged_from_plan()
        self._last_untagged_files = untagged

        if not grouped_files:
            QMessageBox.information(
                self, "파일 정리",
                f"저장목록에서 태그가 설정된 정리 대상이 없습니다.\n"
                f"미태깅 파일: {len(untagged)}개\n\n"
                "먼저 저장목록에서 사용할 파일과 태그를 설정해 주세요.",
            )
            return

        # 태그 있는 파일과 미분류 파일이 혼재하는 경우
        if untagged:
            reply = QMessageBox.question(
                self, "파일 정리",
                f"저장목록에서 태그가 설정된 {len(organize_files)}개 파일만 정리합니다.\n"
                f"미태깅 파일 {len(untagged)}개는 제외됩니다. 계속할까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # 대상 기본 폴더 선택
        base_path = QFileDialog.getExistingDirectory(self, "정리할 기본 폴더 선택")
        if not base_path:
            return  # 취소 → 파일 변경 0건

        # 정리 Preview 빌드
        preview = self.core.build_organize_preview(grouped_files, base_path)

        move_plan = []
        skipped_conflicts = []
        for item in preview:
            if item["has_conflict"]:
                skipped_conflicts.append(item)
                continue
            file_entry = next(
                (f for f in organize_files if f["file_path"] == item["source_path"]),
                None,
            )
            if file_entry:
                move_plan.append({
                    "file_id": file_entry["id"],
                    "file_path": item["source_path"],
                    "target_path": item["target_path"],
                    "file_name": item["file_name"],
                })

        if not move_plan:
            QMessageBox.warning(
                self, "파일 정리",
                "이동할 파일이 없습니다.\n"
                f"충돌(이미 존재하는 파일) {len(skipped_conflicts)}개가 전부 제외되었습니다.",
            )
            return

        # 최종 확인 Dialog
        conflict_note = (
            f"\n충돌(이미 존재 → 제외): {len(skipped_conflicts)}개" if skipped_conflicts else ""
        )
        reply = QMessageBox.question(
            self,
            "파일 정리 최종 확인",
            f"다음 정리를 시작합니다.\n\n"
            f"이동될 파일: {len(move_plan)}개\n"
            f"대상 폴더: {base_path}"
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
        """AI background 분석 완료 처리.

        Batch 12: QMessageBox 모달 없이 Preview(grouped_screen)를 자동 갱신한다.
        사용자 승인 없이 Apply를 자동 실행하지 않는다.
        """
        self._close_untagged_dialog()
        summary = summary or {}
        success_count = summary.get("success", 0)
        failed_list = summary.get("failed", [])

        # Apply 실행 중 → Plan 변경 금지 (Apply 대상이 중간에 바뀌면 안 됨)
        if self._apply_worker and self._apply_worker.isRunning():
            return
        if self._undo_worker and self._undo_worker.isRunning():
            return

        # Stale context 감지 — 다른 폴더/다른 Plan으로 이동한 경우 무시
        if self._analysis_plan_context_id != self._plan_context_id:
            return

        # 분석 완료 후 미분류 목록 갱신
        untagged_remaining = self._get_untagged_from_plan()
        self._last_untagged_files = untagged_remaining

        if success_count > 0:
            # Preview 자동 갱신 (기존 Plan generation 로직 재사용)
            self._refresh_grouped_after_analysis()

            # 배너만 갱신 — 블로킹 모달 없이 사용자가 즉시 Preview를 확인할 수 있음
            banner_text = (
                f"AI 분석 완료 | 새로 분류됨: {success_count}개"
                + (f" | 미분류: {len(untagged_remaining)}개" if untagged_remaining else "")
                + " | 정리 계획이 갱신되었습니다. 파일은 아직 변경되지 않습니다."
            )
            self._grouped_screen.set_banner_text(banner_text)
            # 새 Plan → confirm_btn은 이미 활성화 상태이므로 사용자가 확인 후 승인
        else:
            # 전체 실패 — 배너로 상태 안내, 모달 없음
            banner_text = (
                f"AI 분석 완료 | 태그 생성 실패: {len(failed_list)}개 — 미분류 상태 유지 | "
                "파일은 변경되지 않습니다."
            )
            self._grouped_screen.set_banner_text(banner_text)

    def _on_untagged_analysis_error(self, message: str):
        self._close_untagged_dialog()
        safe_msg = message.split("\n")[0][:200] if message else "알 수 없는 오류"
        QMessageBox.warning(self, "AI 분석 오류", f"AI 분석 중 오류가 발생했습니다:\n{safe_msg}")

    def _refresh_grouped_after_analysis(self):
        """AI 분석 완료 후 grouped screen의 그룹 카드를 갱신한다."""
        scanned = self._last_plan_files
        untagged_set = {
            os.path.normcase(os.path.abspath(p))
            for p in self._last_untagged_files
        }

        folder_files: dict = {}
        for f in scanned:
            if os.path.normcase(os.path.abspath(f)) in untagged_set:
                continue
            folder = str(Path(f).parent)
            kind = self._get_file_kind_by_extension(f)
            name = Path(f).name
            label = name[:15] + "..." if len(name) > 15 else name
            folder_files.setdefault(folder, []).append((kind, label))

        groups_ui = [
            (Path(fp).name or fp, files[:10])
            for fp, files in folder_files.items()
        ]

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

        self._grouped_screen.set_groups(groups_ui)

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

