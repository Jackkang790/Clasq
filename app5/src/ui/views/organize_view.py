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
import os
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QStackedWidget, QScrollArea, QAbstractItemView, QFileDialog,
    QMessageBox,
)

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
        self.setObjectName("infoBanner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        label = QLabel(text)
        label.setObjectName("infoBannerText")
        lay.addWidget(label)
        lay.addStretch()


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
        # files: list[tuple(kind, label, source_path, target_path, has_conflict)]
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
        for kind, label, source_path, target_path, has_conflict in files:
            icons_row.addWidget(_FileIconCard(kind, label))
        icons_row.addStretch()
        outer.addLayout(icons_row)
        for _, label, source_path, target_path, has_conflict in files:
            detail = QLabel(f"{label}\n현재: {source_path}\n대상: {target_path}" + ("  ⚠ 이름 충돌: 번호를 붙여 이동" if has_conflict else ""))
            detail.setWordWrap(True)
            detail.setStyleSheet("color: #6B6C7A; font-size: 11px; padding: 4px 0;")
            outer.addWidget(detail)


def _make_btn(text, primary=False):
    btn = QPushButton(text)
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

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        breadcrumb = QLabel("메인화면 > 정리하기 ...")
        breadcrumb.setObjectName("breadcrumb")
        root.addWidget(breadcrumb)

        root.addWidget(_InfoBanner("Ctrl + 1 을 누르면 백그라운드에서 실행됩니다"))

        header_row = QHBoxLayout()
        title = QLabel("파일 자동 정리")
        title.setObjectName("screenTitle")
        header_row.addWidget(title)
        header_row.addStretch()

        add_path_btn = _make_btn("경로 추가하기")
        add_path_btn.clicked.connect(self._on_add_path)
        auto_btn = _make_btn("자동 정리하기", primary=True)
        auto_btn.clicked.connect(self.autoOrganizeRequested.emit)
        header_row.addWidget(add_path_btn)
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


    def set_rows(self, rows):
        """rows: list[tuple(파일명, 태그, 파일경로)] - REQ-004 파일 목록 표시"""
        self.table.setRowCount(0)
        for r, (name, tag, path) in enumerate(rows):
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(name))
            self.table.setItem(r, 1, QTableWidgetItem(tag))
            self.table.setItem(r, 2, QTableWidgetItem(path))

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

        root.addWidget(_InfoBanner("AI 분석 완료 - 생성된 최적의 파일 정리 계획입니다"))

        header_row = QHBoxLayout()
        title = QLabel("파일 자동 정리")
        title.setObjectName("screenTitle")
        header_row.addWidget(title)
        header_row.addStretch()
        header_row.addWidget(_make_btn("경로 추가하기"))
        header_row.addWidget(_make_btn("자동 정리하기", primary=True))
        root.addLayout(header_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.group_container = QWidget()
        self.group_layout = QVBoxLayout(self.group_container)
        self.group_layout.setSpacing(12)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        self.group_layout.addStretch()
        scroll.setWidget(self.group_container)
        root.addWidget(scroll, stretch=1)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        edit_btn = _make_btn("수정하기")
        edit_btn.clicked.connect(self.editRequested.emit)
        confirm_btn = _make_btn("이대로 정리하기", primary=True)
        confirm_btn.clicked.connect(self.organizeConfirmed.emit)
        bottom_row.addWidget(edit_btn)
        bottom_row.addWidget(confirm_btn)
        root.addLayout(bottom_row)

    def set_groups(self, groups):
        """groups: list[tuple(폴더명, list[tuple(kind, label)])] - REQ-010 그룹화 결과 표시"""
        while self.group_layout.count() > 1:
            item = self.group_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for name, files in groups:
            self.group_layout.insertWidget(self.group_layout.count() - 1, _GroupedFolderCard(name, files))


# ---------------------------------------------------------------------------
# 외부에 노출되는 진짜 뷰: OrganizeView
# ---------------------------------------------------------------------------
class OrganizeView(QWidget):
    """기존 정리 UI에 DB·AI 태깅·실제 파일 이동 기능을 연결하는 뷰."""

    def __init__(self, core=None, parent=None):
        super().__init__(parent)
        self.core = core
        self.grouped_files = {}
        self.organize_base_path = ""
        self._tagging_worker = None
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

    def _on_path_added(self, path):
        """경로 추가 처리 (파일 스캔 및 테이블 업데이트)"""
        if not self.core:
            QMessageBox.information(self, "경로 추가됨", f"다음 폴더가 정리 대상에 추가되었습니다:\n{path}")
            return
 
        # DB에 경로 추가
        result = self.core.registry.add_managed_path(path)
 
        # 파일 스캔
        try:
            scanned_files = self.core.scan_directory_files(path)
 
            if not scanned_files:
                QMessageBox.information(self, "경로 추가됨", 
                    f"경로가 추가되었지만 지원되는 파일이 없습니다:\n{path}")
                return
 
            # 스캔된 파일들을 테이블에 추가
            current_rows = []
            for i in range(self._table_screen.table.rowCount()):
                file_name = self._table_screen.table.item(i, 0).text()
                tag = self._table_screen.table.item(i, 1).text()
                file_path = self._table_screen.table.item(i, 2).text()
                current_rows.append((file_name, tag, file_path))
 
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
 
            self._table_screen.set_rows(current_rows)
 
            # AI 처리 옵션 물어보기
            reply = QMessageBox.question(
                self, 
                "AI 처리", 
                f"{len(scanned_files)}개의 파일을 스캔했습니다.\n지금 AI 태깅을 진행하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No
            )
 
            if reply == QMessageBox.Yes:
                self._start_ai_tagging([path])
            else:
                QMessageBox.information(self, "경로 추가됨", 
                    f"경로가 추가되고 {len(scanned_files)}개 파일이 로드되었습니다:\n{path}")
 
        except Exception as e:
            QMessageBox.critical(self, "오류", f"파일 스캔 중 오류가 발생했습니다:\n{str(e)}")
 
    def _start_ai_tagging(self, paths):
        if self._tagging_worker and self._tagging_worker.isRunning():
            QMessageBox.information(self, "AI 태깅", "이미 태깅 작업이 진행 중입니다.")
            return
        from src.utils.workers import FolderScanAndTagWorker
        self._tagging_worker = FolderScanAndTagWorker(paths, self.core)
        self._tagging_worker.progress.connect(self._on_tagging_progress)
        self._tagging_worker.finished.connect(self._on_tagging_finished)
        self._tagging_worker.error.connect(self._on_tagging_error)
        self._tagging_worker.start()
        QMessageBox.information(self, "AI 태깅", "AI 태깅을 시작합니다.")

    def _on_tagging_progress(self, message):
        print(message)

    def _on_tagging_finished(self, summary=None):
        self._load_files_from_db()
        summary = summary or {}
        QMessageBox.information(self, "AI 태깅", f"AI 태깅 완료: 성공 {summary.get('success', 0)}개, 실패 {len(summary.get('failed', []))}개")

    def _on_tagging_error(self, message):
        QMessageBox.critical(self, "AI 태깅 오류", message)

    def _on_auto_organize(self):
        if not self.core:
            QMessageBox.warning(self, "자동 정리", "코어 시스템이 초기화되지 않았습니다.")
            return
        try:
            self.grouped_files = self.core.group_files_by_tags(self.core.get_files_for_organize())
            if not self.grouped_files:
                QMessageBox.information(self, "정리 대상 없음", "태그가 있는 파일이 없습니다. 먼저 태그를 부착해주세요.")
                return
            base_path = QFileDialog.getExistingDirectory(self, "정리할 기본 폴더 선택 (아직 파일은 이동하지 않습니다)")
            if not base_path:
                return
            self.organize_base_path = base_path
            preview_by_tag = {}
            for plan in self.core.build_organize_preview(self.grouped_files, base_path):
                preview_by_tag.setdefault(plan["tag"], []).append(plan)
            groups_ui = []
            for tag_name, files in self.grouped_files.items():
                cards = []
                for file_info, plan in zip(files, preview_by_tag.get(tag_name, [])):
                    file_name = file_info["file_name"]
                    extension = os.path.splitext(file_name)[1].lower()
                    kind = "image" if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else "doc" if extension in {".txt", ".doc", ".docx", ".pdf"} else "default"
                    cards.append((kind, file_name, plan["source_path"], plan["target_path"], plan["has_conflict"]))
                groups_ui.append((f"{tag_name} 폴더", cards))
            self._grouped_screen.set_groups(groups_ui)
            self._show_grouped()
        except Exception as exc:
            QMessageBox.critical(self, "자동 정리 오류", str(exc))

    def _on_organize_confirmed(self):
        if not self.core or not self.grouped_files:
            QMessageBox.warning(self, "파일 정리", "정리할 그룹 정보가 없습니다.")
            return
        if not self.organize_base_path:
            return
        result = self.core.organize_files(self.grouped_files, self.organize_base_path)
        if result["success"]:
            message = f"이동된 파일: {len(result.get('moved_files', []))}개"
            errors = result.get("errors", [])
            if errors:
                message += f"\n오류: {len(errors)}개\n" + "\n".join(errors[:5])
            QMessageBox.information(self, "정리 완료", message)
            self._load_files_from_db()
            self._show_table()
            self.organize_base_path = ""
        else:
            QMessageBox.critical(self, "정리 실패", result.get("message", "알 수 없는 오류"))

    def set_file_rows(self, rows):
        self._table_screen.set_rows(rows)

    def set_grouped_result(self, groups):
        self._grouped_screen.set_groups(groups)
