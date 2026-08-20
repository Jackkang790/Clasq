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
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QStackedWidget, QScrollArea, QAbstractItemView, QFileDialog,
    QMessageBox, QComboBox, QProgressBar,
)

from src.utils.core import get_files_for_organize, load_registered_files, scan_directory_files
from src.utils.workers import FolderAnalysisPlanWorker, FolderScanAndTagWorker

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
        self.label = QLabel(text)
        self.label.setObjectName("infoBannerText")
        lay.addWidget(self.label)
        lay.addStretch()

    def set_text(self, text):
        self.label.setText(text)


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
    def __init__(self, folder_name, files, total_count=None, parent=None):
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
        count = QLabel(f"{total_count if total_count is not None else len(files)}개 파일")
        count.setObjectName("groupCount")
        title_row.addWidget(count)
        outer.addLayout(title_row)

        icons_row = QHBoxLayout()
        icons_row.setSpacing(10)
        for kind, label in files:
            icons_row.addWidget(_FileIconCard(kind, label))
        icons_row.addStretch()
        outer.addLayout(icons_row)


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

        self.status_banner = _InfoBanner("경로를 추가하면 지원 파일을 검색합니다.")
        root.addWidget(self.status_banner)

        header_row = QHBoxLayout()
        title = QLabel("파일 자동 정리")
        title.setObjectName("screenTitle")
        header_row.addWidget(title)
        header_row.addStretch()

        self.add_path_btn = _make_btn("경로 추가하기")
        self.add_path_btn.clicked.connect(self._on_add_path)
        self.auto_btn = _make_btn("자동 정리하기", primary=True)
        self.auto_btn.clicked.connect(self.autoOrganizeRequested.emit)
        self.batch_combo = QComboBox()
        self.batch_combo.addItem("50개", 50)
        self.batch_combo.addItem("100개", 100)
        self.batch_combo.addItem("전체", None)
        self.stop_btn = _make_btn("중지")
        self.stop_btn.setEnabled(False)
        header_row.addWidget(self.add_path_btn)
        header_row.addWidget(self.batch_combo)
        header_row.addWidget(self.auto_btn)
        header_row.addWidget(self.stop_btn)
        root.addLayout(header_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

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

        page_row = QHBoxLayout()
        page_row.addStretch()
        self.prev_page_btn = _make_btn("이전")
        self.page_label = QLabel("0 / 0")
        self.next_page_btn = _make_btn("다음")
        page_row.addWidget(self.prev_page_btn)
        page_row.addWidget(self.page_label)
        page_row.addWidget(self.next_page_btn)
        page_row.addStretch()
        root.addLayout(page_row)

    def set_rows(self, rows):
        """rows: list[tuple(파일명, 태그, 파일경로)] - REQ-004 파일 목록 표시"""
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))
        for r, (name, tag, path) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(name))
            self.table.setItem(r, 1, QTableWidgetItem(tag))
            self.table.setItem(r, 2, QTableWidgetItem(path))
        self.table.setUpdatesEnabled(True)

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

        self.status_banner = _InfoBanner("AI 분석 완료 - 생성된 최적의 파일 정리 계획입니다")
        self.status_banner.setVisible(False)
        root.addWidget(self.status_banner)

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
        for group in groups:
            name, files = group[:2]
            total_count = group[2] if len(group) > 2 else len(files)
            self.group_layout.insertWidget(
                self.group_layout.count() - 1,
                _GroupedFolderCard(name, files, total_count=total_count),
            )


# ---------------------------------------------------------------------------
# 외부에 노출되는 진짜 뷰: OrganizeView
# ---------------------------------------------------------------------------
class OrganizeView(QWidget):
    """
    MainWindow의 stacked_widget(index 2)에 들어가는 '정리하기' 화면.
    내부적으로 테이블 뷰 <-> 자동 그룹화 뷰를 전환한다.
    """

    def __init__(self, parent=None, db_path="file_manager.db", main_processor=None):
        super().__init__(parent)
        self.db_path = db_path
        self.main_processor = main_processor
        self._selected_path = None
        self._scanned_files = []
        self._analysis_completed = False
        self._analysis_plan = None
        self._plan_worker = None
        self._tag_worker = None
        self._post_batch_stats = None
        self._table_page = 0
        self._table_page_size = 200
        self.setObjectName("organizeView")
        self.setStyleSheet(FALLBACK_QSS)  # 전역 light.qss가 없을 때를 위한 최소 폴백

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._inner_stack = QStackedWidget()
        self._table_screen = _FileTableScreen()
        self._grouped_screen = _GroupedScreen()
        self._inner_stack.addWidget(self._table_screen)    # inner index 0
        self._inner_stack.addWidget(self._grouped_screen)  # inner index 1
        layout.addWidget(self._inner_stack)

        self._table_screen.autoOrganizeRequested.connect(self._on_auto_organize)
        self._table_screen.addPathRequested.connect(self._on_path_added)
        self._table_screen.stop_btn.clicked.connect(self._request_stop)
        self._table_screen.batch_combo.currentIndexChanged.connect(self._show_plan_summary)
        self._table_screen.prev_page_btn.clicked.connect(lambda: self._change_table_page(-1))
        self._table_screen.next_page_btn.clicked.connect(lambda: self._change_table_page(1))
        self._grouped_screen.editRequested.connect(self._show_table)
        self._grouped_screen.organizeConfirmed.connect(self._on_organize_confirmed)

    # ---- 화면 전환 ----
    def _show_grouped(self):
        self._inner_stack.setCurrentWidget(self._grouped_screen)

    def _show_table(self):
        self._inner_stack.setCurrentWidget(self._table_screen)

    def _legacy_on_path_added(self, path):
        if self._tag_worker and self._tag_worker.isRunning():
            QMessageBox.information(self, "AI 분석 중", "현재 분석이 끝난 후 다른 경로를 추가해주세요.")
            return
        self._selected_path = os.path.abspath(path)
        self._scanned_files = scan_directory_files(self._selected_path)
        self._analysis_completed = False
        self._grouped_screen.set_groups([])
        self._grouped_screen.status_banner.setVisible(False)
        self._inner_stack.setCurrentWidget(self._table_screen)
        self._show_scanned_files()
        self._table_screen.status_banner.set_text(
            f"총 {len(self._scanned_files):,}개 파일 · 증분 분석 상태 계산 중..."
        )

    def _show_scanned_files(self):
        started = time.perf_counter()
        total = len(self._scanned_files)
        page_count = (total + self._table_page_size - 1) // self._table_page_size
        if page_count == 0:
            self._table_page = 0
        else:
            self._table_page = min(self._table_page, page_count - 1)
        start = self._table_page * self._table_page_size
        page_files = self._scanned_files[start:start + self._table_page_size]
        records = {row["file_path"]: row for row in load_registered_files(self.db_path, page_files)}
        rows = []
        for file_path in page_files:
            record = records.get(file_path)
            tags = ", ".join(record["tags"]) if record else ""
            rows.append((os.path.basename(file_path), tags, file_path))
        self._table_screen.set_rows(rows)
        self._table_screen.page_label.setText(
            f"{self._table_page + 1 if page_count else 0} / {page_count} · "
            f"총 {total:,}개 중 {len(page_files):,}개 표시"
        )
        self._table_screen.prev_page_btn.setEnabled(self._table_page > 0)
        self._table_screen.next_page_btn.setEnabled(self._table_page + 1 < page_count)
        print(f"[PERF] table refresh: {time.perf_counter() - started:.3f} sec")

    def _change_table_page(self, delta):
        next_page = self._table_page + delta
        page_count = (len(self._scanned_files) + self._table_page_size - 1) // self._table_page_size
        if 0 <= next_page < page_count:
            self._table_page = next_page
            self._show_scanned_files()

    def _legacy_on_auto_organize(self):
        if not self._scanned_files:
            QMessageBox.information(self, "파일 없음", "먼저 정리할 경로를 추가해주세요.")
            return
        if self._analysis_completed:
            self._build_groups_from_db()
            return
        self._start_ai_tagging()

    def _legacy_start_ai_tagging(self):
        if self._tag_worker and self._tag_worker.isRunning():
            return
        self._set_tagging_busy(True)
        self._tag_worker = FolderScanAndTagWorker(
            [self._selected_path],
            main_processor=self.main_processor,
            file_paths=self._scanned_files,
        )
        self._tag_worker.progress.connect(self._on_tagging_progress)
        self._tag_worker.error.connect(self._on_tagging_error)
        self._tag_worker.completed.connect(self._on_tagging_completed)
        self._tag_worker.finished.connect(self._on_tagging_thread_finished)
        self._tag_worker.start()

    def _legacy_on_tagging_progress(self, message):
        self._table_screen.status_banner.set_text(
            f"총 {len(self._scanned_files)}개 파일 · {message}"
        )

    def _legacy_on_tagging_error(self, message):
        self._set_tagging_busy(False)
        QMessageBox.warning(self, "AI 분석 오류", message)

    def _legacy_on_tagging_completed(self):
        self._analysis_completed = True
        self._load_files_from_db()
        self._build_groups_from_db()

    def _legacy_on_tagging_thread_finished(self):
        self._set_tagging_busy(False)
        if self._tag_worker:
            self._tag_worker.deleteLater()
            self._tag_worker = None

    def _legacy_set_tagging_busy(self, busy):
        self._table_screen.add_path_btn.setEnabled(not busy)
        self._table_screen.auto_btn.setEnabled(not busy)

    def _load_files_from_db(self):
        started = time.perf_counter()
        analyzed = get_files_for_organize(self.db_path, self._scanned_files)
        db_elapsed = time.perf_counter() - started
        self._show_scanned_files()
        self._table_screen.status_banner.set_text(
            f"총 {len(self._scanned_files)}개의 지원 파일 · AI 분석 완료 {len(analyzed)}개"
        )
        print(f"[PERF] DB reload: {db_elapsed:.3f} sec")
        return analyzed

    def _legacy_build_groups_from_db(self):
        analyzed = get_files_for_organize(self.db_path, self._scanned_files)
        grouped = {}
        for record in analyzed:
            group_name = record["tags"][0]
            grouped.setdefault(group_name, []).append(record)

        groups = []
        for group_name in sorted(grouped, key=str.casefold):
            records = grouped[group_name]
            preview = [
                (self._icon_kind(record["file_path"]), record["file_name"])
                for record in records[:8]
            ]
            groups.append((group_name, preview, len(records)))

        self._grouped_screen.set_groups(groups)
        self._grouped_screen.status_banner.set_text(
            f"AI 분석 완료 - {len(analyzed)}개 파일을 {len(groups)}개 그룹으로 분류했습니다."
        )
        self._grouped_screen.status_banner.setVisible(True)
        self._inner_stack.setCurrentWidget(self._grouped_screen)

    def _on_path_added(self, path):
        if ((self._tag_worker and self._tag_worker.isRunning())
                or (self._plan_worker and self._plan_worker.isRunning())):
            QMessageBox.information(self, "AI 분석 중", "현재 작업이 끝난 후 다른 경로를 추가해주세요.")
            return
        self._selected_path = os.path.abspath(path)
        self._scanned_files = []
        self._table_page = 0
        self._analysis_plan = None
        self._analysis_completed = False
        self._grouped_screen.set_groups([])
        self._grouped_screen.status_banner.setVisible(False)
        self._inner_stack.setCurrentWidget(self._table_screen)
        self._table_screen.set_rows([])
        self._start_analysis_plan()

    def _start_analysis_plan(self, disable_controls=True):
        if not self._selected_path:
            return
        if disable_controls:
            self._set_planning_busy(True)
        self._plan_worker = FolderAnalysisPlanWorker([self._selected_path], db_path=self.db_path)
        self._plan_worker.progress.connect(self._table_screen.status_banner.set_text)
        self._plan_worker.error.connect(self._on_plan_error)
        self._plan_worker.completed.connect(self._on_plan_completed)
        self._plan_worker.finished.connect(self._on_plan_thread_finished)
        self._plan_worker.start()

    def _on_plan_completed(self, plan):
        self._analysis_plan = plan
        self._scanned_files = list(plan["scanned"])
        self._analysis_completed = plan["counts"]["pending"] == 0
        if self._post_batch_stats is not None:
            stats = self._post_batch_stats
            self._post_batch_stats = None
            self._finish_batch_ui(stats, plan["counts"]["pending"])
        else:
            self._show_scanned_files()
            self._show_plan_summary()

    def _on_plan_error(self, message):
        QMessageBox.warning(self, "파일 검사 오류", message)

    def _on_plan_thread_finished(self):
        self._set_planning_busy(False)
        if self._plan_worker:
            self._plan_worker.deleteLater()
            self._plan_worker = None

    def _show_plan_summary(self):
        if not self._analysis_plan:
            return
        counts = self._analysis_plan["counts"]
        limit = self._table_screen.batch_combo.currentData()
        batch_count = min(counts["pending"], limit) if limit else counts["pending"]
        self._table_screen.status_banner.set_text(
            self._format_plan_statistics(counts, batch_count=batch_count)
        )

    @staticmethod
    def _format_plan_statistics(counts, batch_count=None, refreshed=False):
        analyzed = counts["already_analyzed"] + counts["same_content"]
        analyzed_label = "분석 완료" if refreshed else "기존 분석 완료"
        text = (
            f"총 {counts['scanned']:,}개 · {analyzed_label} {analyzed:,}개 · "
            f"신규 {counts['new']:,}개 · 변경 {counts['changed']:,}개 · "
            f"동일 내용 재사용 {counts['same_content']:,}개 · "
            f"분석 필요 {counts['pending']:,}개"
        )
        if batch_count is not None:
            text += f" · 이번 배치 {batch_count:,}개"
        return text

    def _on_auto_organize(self):
        if not self._scanned_files:
            QMessageBox.information(self, "파일 없음", "먼저 정리할 경로를 추가해주세요.")
            return
        if self._analysis_plan is None:
            self._start_analysis_plan()
            return
        if self._analysis_completed:
            self._build_groups_from_db()
            return
        limit = self._table_screen.batch_combo.currentData()
        pending_count = self._analysis_plan["counts"]["pending"]
        if limit is None and pending_count >= 500:
            answer = QMessageBox.question(
                self, "전체 AI 분석 확인",
                f"{pending_count:,}개의 파일을 모두 AI 분석합니다. 시간이 오래 걸릴 수 있습니다. 계속하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self._start_ai_tagging()

    def _start_ai_tagging(self):
        if self._tag_worker and self._tag_worker.isRunning():
            return
        self._set_tagging_busy(True)
        limit = self._table_screen.batch_combo.currentData()
        self._tag_worker = FolderScanAndTagWorker(
            self._analysis_plan["pending"], main_processor=self.main_processor,
            db_path=self.db_path, batch_limit=limit,
            total_pending=self._analysis_plan["counts"]["pending"],
        )
        self._tag_worker.progress.connect(self._on_tagging_progress)
        self._tag_worker.statistics_changed.connect(self._on_tagging_statistics)
        self._tag_worker.error.connect(self._on_tagging_error)
        self._tag_worker.completed.connect(self._on_tagging_completed)
        self._tag_worker.stopped.connect(self._on_tagging_stopped)
        self._tag_worker.finished.connect(self._on_tagging_thread_finished)
        self._tag_worker.start()

    def _on_tagging_progress(self, message):
        self._table_screen.status_banner.set_text(message)

    def _on_tagging_statistics(self, stats):
        self._table_screen.progress_bar.setMaximum(max(1, stats["batch_total"]))
        self._table_screen.progress_bar.setValue(stats["processed"])
        self._table_screen.status_banner.set_text(
            f"AI 분석 {stats['processed']:,} / {stats['batch_total']:,} · "
            f"성공 {stats['success']:,} · 실패 {stats['failed']:,} · "
            f"변경 감지 {stats.get('stale', 0):,} · "
            f"남은 분석 대상 {stats['remaining']:,}"
        )

    def _on_tagging_error(self, message):
        self._set_tagging_busy(False)
        QMessageBox.warning(self, "AI 분석 오류", message)

    def _on_tagging_completed(self, stats):
        print(f"[PERF] batch completion slot: processed={stats['processed']} success={stats['success']} failed={stats['failed']}")
        self._post_batch_stats = stats
        self._set_tagging_busy(False)
        self._table_screen.status_banner.set_text(
            f"{stats['processed']:,}개 AI 분석 완료 · 파일 상태 갱신 중..."
        )
        self._start_analysis_plan(disable_controls=False)

    def _on_tagging_stopped(self, stats):
        self._post_batch_stats = stats
        self._set_tagging_busy(False)
        self._table_screen.status_banner.set_text(
            f"{stats['processed']:,}개 처리 후 중지 · 파일 상태 갱신 중..."
        )
        self._start_analysis_plan(disable_controls=False)

    def _request_stop(self):
        if self._tag_worker and self._tag_worker.isRunning():
            self._tag_worker.request_stop()
            self._table_screen.status_banner.set_text(
                "중지 요청됨 - 현재 분석 중인 파일이 끝난 후 중지합니다."
            )
            self._table_screen.stop_btn.setEnabled(False)

    def _finish_batch_ui(self, stats, remaining):
        if stats.get("stopped"):
            message = (f"분석 중지 - 성공 {stats['success']:,}개 · 실패 {stats['failed']:,}개 · "
                       f"남은 분석 대상 {remaining:,}개")
        elif remaining == 0:
            message = "AI 분석 완료 - 모든 신규 및 변경 파일 분석 완료"
        else:
            message = f"부분 분석 완료 - {stats['processed']:,}개 처리, {remaining:,}개 남음"
        counts = self._analysis_plan["counts"]
        self._table_screen.status_banner.set_text(
            f"{message}\n{self._format_plan_statistics(counts, refreshed=True)}"
        )
        self._load_files_from_db()
        self._build_groups_from_db(completion_message=message)

    def _on_tagging_thread_finished(self):
        self._set_tagging_busy(False)
        if self._tag_worker:
            self._tag_worker.deleteLater()
            self._tag_worker = None

    def _set_tagging_busy(self, busy):
        self._table_screen.add_path_btn.setEnabled(not busy)
        self._table_screen.auto_btn.setEnabled(not busy)
        self._table_screen.batch_combo.setEnabled(not busy)
        self._table_screen.stop_btn.setEnabled(busy)
        self._table_screen.progress_bar.setVisible(busy)

    def _set_planning_busy(self, busy):
        self._table_screen.add_path_btn.setEnabled(not busy)
        self._table_screen.auto_btn.setEnabled(not busy)
        self._table_screen.batch_combo.setEnabled(not busy)

    def _build_groups_from_db(self, completion_message=None):
        started = time.perf_counter()
        analyzed = get_files_for_organize(self.db_path, self._scanned_files)
        grouped = {}
        for record in analyzed:
            grouped.setdefault(record["tags"][0], []).append(record)
        groups = []
        for group_name in sorted(grouped, key=str.casefold):
            records = grouped[group_name]
            preview = [(self._icon_kind(row["file_path"]), row["file_name"])
                       for row in records[:8]]
            groups.append((group_name, preview, len(records)))
        self._grouped_screen.set_groups(groups)
        self._grouped_screen.status_banner.set_text(
            completion_message or
            f"AI 분석 완료 - {len(analyzed):,}개 파일을 {len(groups):,}개 그룹으로 분류했습니다."
        )
        self._grouped_screen.status_banner.setVisible(True)
        self._inner_stack.setCurrentWidget(self._grouped_screen)
        print(f"[PERF] group refresh: {time.perf_counter() - started:.3f} sec")

    @staticmethod
    def _icon_kind(file_path):
        extension = os.path.splitext(file_path)[1].lower()
        if extension in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"}:
            return "image"
        if extension == ".txt":
            return "txt"
        if extension in {".pdf", ".docx", ".xlsx", ".pptx", ".hwp", ".hwpx"}:
            return "doc"
        return "default"

    def _on_organize_confirmed(self):
        QMessageBox.information(
            self,
            "정리 계획 확인",
            "현재 단계에서는 분석 결과만 표시하며 실제 파일 이동은 수행하지 않습니다.",
        )

    # ---- 외부(컨트롤러)에서 실데이터 주입할 때 쓰는 진입점 ----
    def set_file_rows(self, rows):
        """rows: list[tuple(파일명, 태그, 파일경로)]"""
        self._table_screen.set_rows(rows)

    def set_grouped_result(self, groups):
        """groups: list[tuple(폴더명, list[tuple(kind, label)])]"""
        self._grouped_screen.set_groups(groups)
