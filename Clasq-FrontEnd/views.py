#src/ui/views 현재경로



#=============organize_view.py================
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

        self._load_mock_groups()

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        edit_btn = _make_btn("수정하기")
        edit_btn.clicked.connect(self.editRequested.emit)
        confirm_btn = _make_btn("이대로 정리하기", primary=True)
        confirm_btn.clicked.connect(self.organizeConfirmed.emit)
        bottom_row.addWidget(edit_btn)
        bottom_row.addWidget(confirm_btn)
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


# ---------------------------------------------------------------------------
# 외부에 노출되는 진짜 뷰: OrganizeView
# ---------------------------------------------------------------------------
class OrganizeView(QWidget):
    """기존 정리 UI에 DB·AI 태깅·실제 파일 이동 기능을 연결하는 뷰."""

    def __init__(self, core=None, parent=None):
        super().__init__(parent)
        self.core = core
        self.grouped_files = {}
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
        """AI 태깅 시작"""
        try:
            self._tagging_worker.progress.connect(self._on_tagging_progress)
            self._tagging_worker.finished.connect(self._on_tagging_finished)
            self._tagging_worker.error.connect(self._on_tagging_error)
            self._tagging_worker.start()
 
            QMessageBox.information(self, "AI 태깅", "AI 태깅을 시작합니다...")
 
        except Exception as e:
            QMessageBox.critical(self, "오류", f"AI 태깅 시작 중 오류:\n{str(e)}")
 
    def _on_tagging_progress(self, message):
        """태깅 진행 상황"""
        print(f"태깅 진행: {message}")
 
    def _on_tagging_finished(self):
        """태깅 완료 후 테이블 새로고침"""
        QMessageBox.information(self, "AI 태깅 완료", "AI 태깅이 완료되었습니다!")
        self._load_files_from_db()
 
    def _on_tagging_error(self, error_message):
        """태깅 에러"""
        QMessageBox.critical(self, "AI 태깅 오류", f"태깅 중 오류:\n{error_message}")

    def _start_ai_tagging(self, paths):
        from src.utils.workers import FolderScanAndTagWorker
        self._tagging_worker = FolderScanAndTagWorker(paths, self.core)
        self._tagging_worker.progress.connect(lambda message: print(message))
        self._tagging_worker.finished.connect(self._on_tagging_finished)
        self._tagging_worker.error.connect(self._on_tagging_error)
        self._tagging_worker.start()
        QMessageBox.information(self, "AI 태깅", "AI 태깅을 시작합니다.")

    def _on_tagging_finished(self):
        self._load_files_from_db()
        QMessageBox.information(self, "AI 태깅", "AI 태깅이 완료되었습니다.")

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
            groups_ui = []
            for tag_name, files in self.grouped_files.items():
                cards = []
                for file_info in files[:10]:
                    file_name = file_info["file_name"]
                    extension = os.path.splitext(file_name)[1].lower()
                    kind = "image" if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else "doc" if extension in {".txt", ".doc", ".docx", ".pdf"} else "default"
                    cards.append((kind, file_name[:15] + "..." if len(file_name) > 15 else file_name))
                groups_ui.append((f"{tag_name} 폴더", cards))
            self._grouped_screen.set_groups(groups_ui)
            self._show_grouped()
        except Exception as exc:
            QMessageBox.critical(self, "자동 정리 오류", str(exc))

    def _on_organize_confirmed(self):
        if not self.core or not self.grouped_files:
            QMessageBox.warning(self, "파일 정리", "정리할 그룹 정보가 없습니다.")
            return
        base_path = QFileDialog.getExistingDirectory(self, "파일을 정리할 기본 폴더 선택")
        if not base_path:
            return
        result = self.core.organize_files(self.grouped_files, base_path)
        if result["success"]:
            message = f"이동된 파일: {len(result.get('moved_files', []))}개"
            errors = result.get("errors", [])
            if errors:
                message += f"\n오류: {len(errors)}개\n" + "\n".join(errors[:5])
            QMessageBox.information(self, "정리 완료", message)
            self._load_files_from_db()
            self._show_table()
        else:
            QMessageBox.critical(self, "정리 실패", result.get("message", "알 수 없는 오류"))

    def set_file_rows(self, rows):
        self._table_screen.set_rows(rows)

    def set_grouped_result(self, groups):
        self._grouped_screen.set_groups(groups)
#=============organize_view.py================
#===============saved_view.py===================
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class SavedView(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        # 전체 메인 레이아웃
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # 1. 상단 백그라운드 안내 배너 (퍼플 연한 배경 스타일)
        notice_banner = QLabel("Ctrl + 1 을 누르면 백그라운드에서 실행됩니다")
        notice_banner.setObjectName("noticeBanner")
        notice_banner.setAlignment(Qt.AlignCenter)
        notice_banner.setStyleSheet("""
            QLabel#noticeBanner {
                background-color: #F0EDFE;
                color: #6C5CE7;
                font-size: 13px;
                font-weight: bold;
                padding: 12px;
                border-radius: 8px;
            }
        """)
        main_layout.addWidget(notice_banner)

        # 2. 헤더 영역 (타이틀 + 수정하기 버튼)
        header_layout = QHBoxLayout()

        title_label = QLabel("태그 저장 목록")
        title_label.setStyleSheet("""
            font-size: 22px;
            font-weight: bold;
            color: #1A1A1A;
        """)

        # 이미지의 '자동 정리하기' 메인 버튼 스타일 적용
        self.edit_btn = QPushButton("수정하기")
        self.edit_btn.setFixedSize(110, 38)
        self.edit_btn.setStyleSheet("""
            QPushButton {
                background-color: #6C5CE7;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #5B4BC4;
            }
            QPushButton:pressed {
                background-color: #4A3BB1;
            }
        """)
        self.edit_btn.clicked.connect(self.on_save_changes)

        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.edit_btn)

        main_layout.addLayout(header_layout)

        # 3. 저장 태그 리스트 (QTableWidget)
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setRowCount(3)
        self.table.setHorizontalHeaderLabels(["파일명", "태그", "파일 경로"])

        # 인라인 편집 설정
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked)

        # 더미 데이터 세팅
        dummy_data = [
            ("프로젝트_보고서.pdf", "업무, PDF", "C:/Documents/프로젝트_보고서.pdf"),
            ("이미지_에셋.zip", "디자인, 원본", "D:/Assets/이미지_에셋.zip"),
            ("결산_마감.xlsx", "재무, 2026", "C:/Work/결산_마감.xlsx"),
        ]

        for row, (filename, tag, path) in enumerate(dummy_data):
            item_filename = QTableWidgetItem(filename)
            item_tag = QTableWidgetItem(tag)
            item_path = QTableWidgetItem(path)

            item_filename.setTextAlignment(Qt.AlignCenter)
            item_tag.setTextAlignment(Qt.AlignCenter)
            item_path.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            self.table.setItem(row, 0, item_filename)
            self.table.setItem(row, 1, item_tag)
            self.table.setItem(row, 2, item_path)

        # 테이블 헤더 디자인 및 레이아웃 설정
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)

        # 이미지 디자인 반영 (둥근 테두리, 연보라 헤더, 선택/포커스 행 스타일)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #FFFFFF;
                border: 1px solid #EBEBEE;
                border-radius: 10px;
                gridline-color: transparent;
                font-size: 13px;
                color: #2D3436;
            }
            QHeaderView::section {
                background-color: #F8F9FA;
                color: #636E72;
                font-size: 13px;
                font-weight: bold;
                padding: 10px;
                border: none;
                border-bottom: 1px solid #EBEBEE;
            }
            QTableWidget::item {
                padding: 8px;
                border-bottom: 1px solid #F1F2F6;
            }
            QTableWidget::item:selected {
                background-color: #F0EDFE;
                color: #2D3436;
            }
            QTableWidget::item:focus {
                background-color: #E0D9FC;
                color: #000000;
            }
        """)

        main_layout.addWidget(self.table, 1)

    # 4. 수정하기 버튼 클릭 시 확인 팝업 및 저장 로직
    def on_save_changes(self):
        reply = QMessageBox.question(
            self,
            "수정 확인",
            "수정 사항을 저장하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.save_to_db()

    def save_to_db(self):
        updated_data = []
        for row in range(self.table.rowCount()):
            filename = (
                self.table.item(row, 0).text() if self.table.item(row, 0) else ""
            )
            tag = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
            path = (
                self.table.item(row, 2).text() if self.table.item(row, 2) else ""
            )
            updated_data.append((filename, tag, path))

        print(f"[DB 저장 완료] 업데이트된 목록: {updated_data}")
        QMessageBox.information(self, "완료", "성공적으로 DB에 반영되었습니다.")
#===============saved_view.py===================
#================search_view.py===================
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

# 신규 위젯 임포트
from src.ui.widgets.fileupload_view import FileUploadView
from src.utils.search_engine import SearchEngine
from src.utils.workers import FolderScanAndTagWorker

# 문장에서 확장자 필터를 뽑아낼 때 쓰는 후보 목록.
# SearchEngine.STOP_WORDS와 겹치는 확장자 표기를 그대로 재사용한다.
_EXTENSION_CANDIDATES = [
    "pdf", "hwp", "hwpx", "docx", "xlsx", "pptx",
    "png", "jpg", "jpeg", "gif", "mp3", "mp4",
]

class QueryProcessWorker(QThread):
    """Ollama 기반 자연어 검색을 UI 스레드 밖에서 처리합니다."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, core, query, parent=None):
        super().__init__(parent)
        self.core = core
        self.query = query

    def run(self):
        try:
            self.finished.emit(self.core.process_user_query(self.query))
        except Exception as exc:
            self.error.emit(str(exc))

# src/ui/views/search_view.py

class SearchView(QWidget):
    def __init__(self, core=None, parent=None, search_engine=None, query_parser=None):  # core 매개변수 추가
        super().__init__(parent)
        self.core = core

        """
        search_engine: SearchEngine 인스턴스를 밖에서 주입할 수 있다.
                       (DB 경로를 다르게 쓰거나 테스트용 mock을 넣고 싶을 때)
        query_parser:  자연어 문장(str) -> parsed_data(dict)로 바꾸는 함수.
                       REQ-011의 실제 LLM 의도 파서가 준비되면 이 인자로 갈아끼우면 된다.
                       시그니처: (text: str) -> dict  (SearchEngine.process_query_result가 먹는 형태)
        """
        self.setAcceptDrops(True)

        self.search_engine = search_engine or (
            core.search_engine if core is not None else SearchEngine()
        )
        self._query_parser = query_parser or (
            core.query_parser.parse_user_query
            if core is not None else self._parse_natural_query
        )

        self.init_ui()

    def init_ui(self):
        self.setStyleSheet("""
            QLabel#mainTitle {
                font-size: 26px;
                font-weight: bold;
                color: #212529;
                margin-bottom: 20px;
            }

            QLineEdit.searchInput {
                border: 1.5px solid #CED4DA;
                border-radius: 20px;
                padding: 8px 18px;
                font-size: 14px;
                background-color: #FFFFFF;
                color: #212529;
            }
            QLineEdit.searchInput:focus {
                border: 1.5px solid #6C5CE7;
            }

            QScrollArea {
                border: none;
                background-color: transparent;
            }

            QLabel.userBubble {
                background-color: #E9ECEF;
                color: #212529;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 14px;
            }

            QLabel.aiBubble {
                background-color: #FFFFFF;
                color: #212529;
                border: 1px solid #E0E0E0;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 14px;
            }

            QLabel.resultBubble {
                background-color: #F5F4FF;
                color: #212529;
                border: 1px solid #DCD6FF;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 13px;
            }

            QLabel.errorBubble {
                background-color: #FDEDEC;
                color: #C0392B;
                border: 1px solid #F5B7B1;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 14px;
            }

            QPushButton.sendBtn {
                background-color: #6C5CE7;
                color: white;
                border: none;
                border-radius: 18px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton.sendBtn:hover {
                background-color: #5A4AD1;
            }
        """)

        self.stacked_layout = QStackedLayout(self)

        # 1. 초기 화면 (Index 0)
        self.init_widget = QWidget()
        init_layout = QVBoxLayout(self.init_widget)
        init_layout.setAlignment(Qt.AlignCenter)

        title = QLabel("무엇을 도와드릴까요?")
        title.setObjectName("mainTitle")
        title.setAlignment(Qt.AlignCenter)

        self.initial_input = QLineEdit()
        self.initial_input.setProperty("class", "searchInput")
        self.initial_input.setFixedWidth(420)
        self.initial_input.setFixedHeight(44)
        self.initial_input.setPlaceholderText("무엇이든 입력해보세요!")
        self.initial_input.returnPressed.connect(self.on_initial_search)

        init_layout.addWidget(title)
        init_layout.addWidget(self.initial_input)

        # 2. 채팅 화면 (Index 1)
        self.chat_widget = QWidget()
        chat_main_layout = QVBoxLayout(self.chat_widget)
        chat_main_layout.setContentsMargins(20, 20, 20, 20)

        # 스크롤 영역
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(10, 10, 10, 10)
        self.chat_layout.setSpacing(12)
        self.chat_layout.addStretch()

        self.scroll_area.setWidget(self.chat_container)
        chat_main_layout.addWidget(self.scroll_area, 1)

        # ★ 기존 bottom_input_layout 대신 신규 위젯 배치
        self.chat_input_widget = FileUploadView()
        self.chat_input_widget.message_submitted.connect(self.on_chat_search)
        self.chat_input_widget.file_attached.connect(self.on_file_attached)

        chat_main_layout.addWidget(self.chat_input_widget)

        self.stacked_layout.addWidget(self.init_widget)  # Index 0
        self.stacked_layout.addWidget(self.chat_widget)  # Index 1
        self.stacked_layout.setCurrentIndex(0)

    def on_initial_search(self):
        query = self.initial_input.text().strip()
        if not query:
            return

        self.stacked_layout.setCurrentIndex(1)
        self.process_query(query)

    def on_chat_search(self, query: str):
        self.process_query(query)

    def on_file_attached(self, file_path: str):
        if self.stacked_layout.currentIndex() == 0:
            self.stacked_layout.setCurrentIndex(1)
        self.add_message(f"📎 [파일 첨부]: {file_path}", is_user=True)
        if self.core is None:
            self.add_message(f"'{file_path}' 파일을 분석할 준비가 되었습니다.", is_user=False)
            return
        self.add_message(f"'{file_path}' 파일을 분석 중입니다...", is_user=False)
        self._file_worker = FolderScanAndTagWorker([file_path], self.core)
        self._file_worker.finished.connect(
            lambda: self.add_message("파일 분석과 태그 저장이 완료되었습니다.", is_user=False)
        )
        self._file_worker.error.connect(self._on_query_error)
        self._file_worker.start()
    def process_query(self, query: str):
        self.add_message(query, is_user=True)
        if self.core is not None:
            self._query_worker = QueryProcessWorker(self.core, query, self)
            self._query_worker.finished.connect(self._on_query_result)
            self._query_worker.error.connect(self._on_query_error)
            self._query_worker.start()
            return

        # 코어가 없는 단위 테스트·미리보기 상황에서는 기존 동기 경로를 유지합니다.
        parsed_data = self._query_parser(query)
        if parsed_data.get("status") == "SUCCESS":
            parsed_data = parsed_data["data"]
        try:
            self._display_query_result(self.search_engine.process_query_result(parsed_data))
        except Exception as exc:
            self._on_query_error(str(exc))

    def _on_query_result(self, result):
        self._display_query_result(result)

    def _on_query_error(self, message):
        self.add_message(f"⚠️ 검색 중 오류가 발생했습니다: {message}", is_user=False, kind="error")

    def _display_query_result(self, result):
        action = result.get("action")
        message = result.get("message", "")
        data = result.get("data", [])
        if action == "UPDATE_TABLE":
            self.add_message(message, is_user=False)
            self._render_search_results(data)
        elif action == "SHOW_CHAT":
            self.add_message(message, is_user=False)
        else:
            self.add_message(f"⚠️ {message or '요청을 처리하지 못했습니다.'}", is_user=False, kind="error")
    def _render_search_results(self, rows):
        """
        SearchEngine이 돌려주는 (id, file_name, file_path, ai_comment, category) 튜플 목록을
        채팅 버블 형태로 하나씩 표시한다.
        """
        MAX_SHOWN = 10
        for row in rows[:MAX_SHOWN]:
            _id, file_name, file_path, ai_comment, category, *_ = row
            lines = [f"📄 {file_name}"]
            if category:
                lines.append(f"분류: {category}")
            if file_path:
                lines.append(f"경로: {file_path}")
            if ai_comment:
                lines.append(f"메모: {ai_comment}")
            self.add_message("\n".join(lines), is_user=False, kind="result")

        remaining = len(rows) - MAX_SHOWN
        if remaining > 0:
            self.add_message(f"...외 {remaining}건 더 있습니다.", is_user=False)

    # -----------------------------------------------------------------
    # 임시 자연어 파서 (TODO: REQ-011 LLM 의도 파서로 교체 예정)
    # -----------------------------------------------------------------
    def _parse_natural_query(self, text: str) -> dict:
        """
        아주 단순한 규칙 기반 임시 파서.
        - 문장에서 알려진 확장자 단어를 뽑아 target_extension으로 분리
        - 나머지 단어는 전부 query_keywords로 넘겨서 SearchEngine의
          불용어 제거/동의어 확장/AND->OR 폴백 로직이 실제 필터링을 하도록 위임한다.
        - 항상 "@검색"으로 라우팅한다 (자유 대화 의도 분류는 LLM 파서가 붙기 전까진 생략).
        """
        words = text.strip().split()
        extensions = []
        keywords = []

        for w in words:
            w_clean = w.strip(".,!?").lower()
            if w_clean in _EXTENSION_CANDIDATES:
                extensions.append(w_clean)
            else:
                keywords.append(w)

        return {
            "@TYPE": "@검색",
            "query_keywords": keywords,
            "target_extension": extensions,
        }

    # -----------------------------------------------------------------
    # 채팅 버블 렌더링
    # -----------------------------------------------------------------
    def add_message(self, text: str, is_user: bool = True, kind: str = "normal"):
        """
        kind: "normal" | "result" | "error"
        - normal: 기존 userBubble/aiBubble
        - result: 검색 결과 1건을 나타내는 연보라색 버블
        - error : 에러/경고를 나타내는 빨간 톤 버블
        """
        row_layout = QHBoxLayout()
        bubble = QLabel(text)
        bubble.setWordWrap(True)

        if is_user:
            bubble.setProperty("class", "userBubble")
            row_layout.addStretch()
            row_layout.addWidget(bubble)
        else:
            if kind == "result":
                bubble.setProperty("class", "resultBubble")
            elif kind == "error":
                bubble.setProperty("class", "errorBubble")
            else:
                bubble.setProperty("class", "aiBubble")
            row_layout.addWidget(bubble)
            row_layout.addStretch()

        bubble.style().unpolish(bubble)
        bubble.style().polish(bubble)

        self.chat_layout.insertLayout(self.chat_layout.count() - 1, row_layout)
        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        QApplication.processEvents()
        v_bar = self.scroll_area.verticalScrollBar()
        v_bar.setValue(v_bar.maximum())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            self.on_file_attached(file_path)
#=======================search_view.py===========================
#======================settings_view.py==========================
import os
import json
from pathlib import Path
from PySide6.QtWidgets import (
    QMenu, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QPushButton, QLabel, QTableWidget,
    QTableWidgetItem, QCheckBox, QHeaderView, QAbstractItemView, QLineEdit,
    QInputDialog, QMessageBox
)
from PySide6.QtCore import Qt, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QIcon, QPainter, QColor
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFileDialog
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QStyle, QStyleOptionButton
from src.utils.workers import FolderScanAndTagWorker

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ASSETS_DIR = PROJECT_ROOT / "assets"
ICONS_DIR = ASSETS_DIR / "styles" / "icons"
PRESET_PATH = ASSETS_DIR / "preset.json"



# ================================
# 전체 체크박스 헤더
# ================================
class CheckBoxHeader(QHeaderView):
    def __init__(self, orientation, parent=None, settings_view=None):
        super().__init__(orientation, parent)
        self.checked = False
        self.settings_view = settings_view

    def paintSection(self, painter, rect, logicalIndex):
        super().paintSection(painter, rect, logicalIndex)
        if logicalIndex == 0:
            option = QStyleOptionButton()
            option.rect = QRect(
                rect.center().x() - 8,
                rect.center().y() - 8,
                16, 16
            )
            option.state = (
                QStyle.State_On if self.checked else QStyle.State_Off
            )
            self.style().drawControl(QStyle.CE_CheckBox, option, painter)

    def mousePressEvent(self, event):
        index = self.logicalIndexAt(event.pos())
        if index == 0:
            self.checked = not self.checked
            if self.settings_view:
                self.settings_view.toggle_all(self.checked)
            self.viewport().update()
        else:
            super().mousePressEvent(event)


# ================================
# 토글 스위치 위젯
# ================================
class ToggleSwitch(QWidget):
    """내부 구현 토글 스위치 위젯"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(50, 26)
        self.setCursor(Qt.PointingHandCursor)
        self._checked = False
        self._circle_position = 3
        self.animation = QPropertyAnimation(self, b"circle_position")
        self.animation.setDuration(200)
        self.animation.setEasingCurve(QEasingCurve.InOutQuad)

    def get_circle_position(self):
        return self._circle_position

    def set_circle_position(self, pos):
        self._circle_position = pos
        self.update()

    circle_position = Property(float, get_circle_position, set_circle_position)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._checked = not self._checked
            self.animation.stop()
            self.animation.setStartValue(self._circle_position)
            self.animation.setEndValue(27 if self._checked else 3)
            self.animation.start()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg_color = QColor("#007ACC") if self._checked else QColor("#CCCCCC")
        p.setBrush(bg_color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 13, 13)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(int(self._circle_position), 3, 20, 20)


# ================================
# 메인 설정 뷰
# ================================
class SettingsView(QWidget):
    def __init__(self, stacked_widget, core=None, parent=None):
        super().__init__(parent)
        self.stacked_widget = stacked_widget
        self.core = core
        self.setObjectName("settingsView")
        self.init_layout()
        self.init_ui()
        if self.core:
            self.load_paths_from_db()

    def init_ui(self):
        self.setStyleSheet("""
        QWidget#settingsView {
            background-color: #FFFFFF;
            color: #2D3436;
            font-size: 10pt;
        }
        QLabel#title {
            padding: 2px;
            font-size: 18pt;
            font-weight: bold;
            color: #1A1A1A;
        }
        QLabel#toglename {
            background: transparent;
            color: #2D3436;
        }
        
        /* 메인 포인트 버튼 (추가) */
        QPushButton#addRoot {
            padding: 8px 16px;
            border-radius: 8px;
            background-color: #6C5CE7;
            color: white;
            font-weight: bold;
            border: none;
        }
        QPushButton#addRoot:hover { background-color: #5B4BC4; }
        QPushButton#addRoot:pressed { background-color: #4A3BB1; }

        /* 보조 버튼 (저장, 새로고침, 초기화) */
        QPushButton#savebtn, QPushButton#reloadbtn, QPushButton#clearbtn {
            padding: 8px 16px;
            border-radius: 8px;
            background-color: #FFFFFF;
            color: #2D3436;
            font-weight: bold;
            border: 1px solid #EBEBEE;
        }
        QPushButton#savebtn:hover,
        QPushButton#reloadbtn:hover,
        QPushButton#clearbtn:hover { 
            background-color: #F0EDFE; 
            color: #6C5CE7;
            border-color: #D6CEFC;
        }
        QPushButton#savebtn:pressed,
        QPushButton#reloadbtn:pressed,
        QPushButton#clearbtn:pressed { 
            background-color: #E0D9FC; 
            color: #5B4BC4;
        }

        QPushButton#backbtn { background: transparent; border: none; }

        /* 그룹박스 & 테이블 레이아웃 */
        QGroupBox {
            background-color: #FFFFFF;
            border: 1px solid #EBEBEE;
            border-radius: 10px;
        }
        QGroupBox#tablebox {
            background: #FAFAFC;
            border: 1px solid #EBEBEE;
            border-radius: 10px;
            padding: 5px;
        }
        QTableWidget {
            background-color: #FFFFFF;
            border: 1px solid #EBEBEE;
            gridline-color: transparent;
            border-radius: 8px;
            color: #2D3436;
        }
        QHeaderView::section {
            background-color: #F8F9FA;
            color: #636E72;
            font-weight: bold;
            border: none;
            border-bottom: 1px solid #EBEBEE;
            padding: 6px;
        }

        /* 체크박스 */
        QCheckBox { spacing: 6px; color: #2D3436; }
        QCheckBox::indicator {
            width: 16px; height: 16px;
            border: 1px solid #DCDDE1;
            border-radius: 4px;
            background-color: #FFFFFF;
        }
        QCheckBox::indicator:hover { border: 1px solid #6C5CE7; }
        QCheckBox::indicator:checked {
            background-color: #6C5CE7;
            border: 1px solid #6C5CE7;
            image: url(__CHECK_ICON__);
        }
        QTableWidget QWidget { background: transparent; }

        /* 삭제 버튼 (포인트 레드 유지 및 모던화) */
        QPushButton#delRoot {
            padding: 8px 16px;
            border-radius: 8px;
            background-color: #EF4444;
            color: white;
            font-weight: bold;
            border: none;
        }
        QPushButton#delRoot:hover { background-color: #DC2626; }
        QPushButton#delRoot:pressed { background-color: #B91C1C; }
    """.replace("__CHECK_ICON__", (ICONS_DIR / "check.svg").as_posix()))

    def toggle_all(self, checked):
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(checked)

    def init_layout(self):
        mainLayout = QVBoxLayout()
        header = QHBoxLayout()
        option = QGroupBox('')
        optionlayout = QHBoxLayout()
        option_main_layout = QVBoxLayout()
        middlelayout = QHBoxLayout()

        tablebox = QGroupBox('')
        tablebox.setObjectName("tablebox")
        tablelayout = QVBoxLayout()
        btnlayout = QHBoxLayout()

        # 상단 요소
        backbtn = QPushButton()
        backbtn.setIcon(QIcon(str(ICONS_DIR / "home.svg")))
        backbtn.setIconSize(QSize(24, 24))
        backbtn.setObjectName("backbtn")
        backbtn.clicked.connect(self.go_search)

        title = QLabel('파일경로 지정')
        title.setObjectName("title")

        # 프리셋 저장 버튼
        savebtn = QPushButton('프리셋 저장하기')
        savebtn.setObjectName("savebtn")

        # 프리셋 이름 입력 위젯
        self.preset_input_widget = QWidget()
        preset_input_layout = QHBoxLayout(self.preset_input_widget)
        self.preset_name_input = QLineEdit()
        self.preset_name_input.setPlaceholderText("프리셋 이름을 입력하세요")
        preset_save_btn = QPushButton("저장")
        preset_save_btn.setObjectName("presetSaveBtn")
        preset_cancel_btn = QPushButton("취소")
        preset_cancel_btn.setObjectName("presetCancelBtn")
        preset_input_layout.addWidget(self.preset_name_input)
        preset_input_layout.addWidget(preset_save_btn)
        preset_input_layout.addWidget(preset_cancel_btn)
        self.preset_input_widget.hide()

        savebtn.clicked.connect(self.show_preset_input)
        preset_save_btn.clicked.connect(self.save_preset)
        preset_cancel_btn.clicked.connect(self.hide_preset_input)

        reloadbtn = QPushButton('프리셋 불러오기')
        reloadbtn.setObjectName("reloadbtn")
        reloadbtn.clicked.connect(self.load_preset)

        togleName = QLabel('자동')
        toggle = ToggleSwitch()
        togleName.setObjectName("toglename")

        clearbtn = QPushButton('태그부착')
        clearbtn.setObjectName("clearbtn")
        clearbtn.clicked.connect(self.start_tagging)

        delRoot = QPushButton('경로삭제')
        delRoot.setObjectName("delRoot")
        delRoot.clicked.connect(self.delete_selected_paths)

        addRoot = QPushButton('경로추가')
        addRoot.setObjectName("addRoot")
        addRoot.clicked.connect(self.add_path)

        # 테이블 설정
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["", "번호", "폴더이름", "파일경로"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)

        tableheader = CheckBoxHeader(Qt.Horizontal, self.table, self)
        self.table.setHorizontalHeader(tableheader)

        tableheader = self.table.horizontalHeader()
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 60)
        tableheader.setSectionResizeMode(0, QHeaderView.Fixed)
        tableheader.setSectionResizeMode(1, QHeaderView.Fixed)
        tableheader.setSectionResizeMode(2, QHeaderView.Stretch)
        tableheader.setSectionResizeMode(3, QHeaderView.Stretch)

        # 레이아웃 조립
        header.addWidget(title)
        header.addStretch()
        header.addWidget(backbtn)

        optionlayout.addWidget(savebtn)
        optionlayout.addWidget(reloadbtn)
        optionlayout.addStretch()
        optionlayout.addWidget(togleName)
        optionlayout.addWidget(toggle)
        optionlayout.addWidget(clearbtn)
        option_main_layout.addLayout(optionlayout)
        option_main_layout.addWidget(self.preset_input_widget)
        option.setLayout(option_main_layout)

        btnlayout.addStretch()
        btnlayout.addWidget(delRoot)
        btnlayout.addWidget(addRoot)
        tablelayout.addLayout(btnlayout)
        tablelayout.addWidget(self.table)
        tablebox.setLayout(tablelayout)

        mainLayout.addLayout(header)
        mainLayout.addWidget(option)
        mainLayout.addLayout(middlelayout)
        mainLayout.addWidget(tablebox, 1)
        self.setLayout(mainLayout)

    # ================================
    # 프리셋 입력창 표시/숨김
    # ================================
    def show_preset_input(self):
        if self.preset_input_widget.isVisible():
            if not self.preset_name_input.text().strip():
                self.hide_preset_input()
                return
        self.preset_input_widget.show()
        self.preset_name_input.setFocus()

    def hide_preset_input(self):
        self.preset_name_input.clear()
        self.preset_input_widget.hide()

    # ================================
    # 파일 / 폴더 선택
    # ================================
    def add_path(self):
        """파일 또는 폴더를 선택해서 테이블에 추가한다."""
        menu = QMenu(self)
        file_action = menu.addAction("파일 선택")
        folder_action = menu.addAction("폴더 선택")

        button = self.sender()
        if button:
            pos = button.mapToGlobal(button.rect().bottomLeft())
            action = menu.exec(pos)
        else:
            action = menu.exec(self.mapToGlobal(self.rect().center()))

        if action == file_action:
            file_paths, _ = QFileDialog.getOpenFileNames(
                self, "파일 선택", "", "모든 파일 (*.*)"
            )
            for file_path in file_paths:
                if file_path:
                    selected_path = os.path.abspath(file_path)
                    self.add_table_item(selected_path, True)
                    if self.core:
                        self.core.registry.add_managed_path(selected_path)
        elif action == folder_action:
            folder_path = QFileDialog.getExistingDirectory(self, "폴더 선택")
            if folder_path:
                selected_path = os.path.abspath(folder_path)
                self.add_table_item(selected_path, True)
                if self.core:
                    self.core.registry.add_managed_path(selected_path)

    # ================================
    # 파일 탐색
    # ================================
    def scan_files(self, target_path):
        """선택한 파일/폴더에서 분석할 파일 목록을 반환한다."""
        target_path = os.path.abspath(target_path)
        if os.path.isfile(target_path):
            return [target_path]
        if os.path.isdir(target_path):
            file_list = []
            for root, dirs, files in os.walk(target_path):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    if os.path.isfile(file_path):
                        file_list.append(os.path.abspath(file_path))
            return file_list
        return []

    # ================================
    # 확장자 분석
    # ================================
    def extract_extensions(self, file_list):
        """파일 목록에서 중복 없는 확장자 목록을 반환한다."""
        extensions = set()
        for file_path in file_list:
            _, extension = os.path.splitext(file_path)
            if extension:
                extensions.add(extension.lower())
        return sorted(extensions)

    

    # ================================
    # 프리셋 저장
    # ================================
    def save_preset(self):
        """체크된 경로만 프리셋에 저장한다."""

        # 프리셋 이름
        preset_name = self.preset_name_input.text().strip()

        if not preset_name:
            QMessageBox.warning(
                self,
                "프리셋 저장",
                "프리셋 이름을 입력해주세요."
            )
            self.preset_name_input.setFocus()
            return

        # 테이블에 데이터가 없는 경우
        if self.table.rowCount() == 0:
            QMessageBox.warning(
                self,
                "프리셋 저장",
                "먼저 파일 또는 폴더를 추가해주세요."
            )
            return

        # ================================
        # 체크된 항목만 수집
        # ================================
        target_data = []
        all_extensions = set()

        for row in range(self.table.rowCount()):

            # 체크박스가 들어있는 위젯
            widget = self.table.cellWidget(row, 0)

            if widget is None:
                continue

            checkbox = widget.findChild(QCheckBox)

            # 체크박스가 없거나 체크되지 않았다면 건너뜀
            if checkbox is None or not checkbox.isChecked():
                continue

            # 파일 경로
            path_item = self.table.item(row, 3)

            if path_item is None:
                continue

            target_path = path_item.text().strip()

            if not target_path:
                continue

            # ================================
            # 파일 분석
            # ================================
            files = self.scan_files(target_path)
            extensions = self.extract_extensions(files)

            all_extensions.update(extensions)

            # ================================
            # 체크된 항목만 저장 데이터에 추가
            # ================================
            target_data.append({
                "name": os.path.basename(
                    os.path.normpath(target_path)
                ),
                "path": target_path,
                "type": (
                    "file"
                    if os.path.isfile(target_path)
                    else "folder"
                ),
                "extensions": extensions
            })

        # ================================
        # 체크된 항목이 하나도 없는 경우
        # ================================
        if not target_data:
            QMessageBox.warning(
                self,
                "프리셋 저장",
                "저장할 경로를 하나 이상 선택해주세요."
            )
            return

        # ================================
        # JSON 파일 경로
        # ================================
        file_path = PRESET_PATH

        os.makedirs(
            file_path.parent,
            exist_ok=True
        )

        # ================================
        # 기존 JSON 읽기
        # ================================
        data = {
            "presets": []
        }

        if os.path.exists(file_path):
            try:
                with open(
                    file_path,
                    "r",
                    encoding="utf-8"
                ) as f:
                    data = json.load(f)

            except (json.JSONDecodeError, OSError):
                data = {
                    "presets": []
                }

        # ================================
        # 기존 형식 호환
        # ================================
        if "presets" not in data:

            old_preset = {
                "preset_name": data.get(
                    "preset_name",
                    ""
                ),
                "targets": data.get(
                    "folders",
                    []
                ),
                "extensions": data.get(
                    "extensions",
                    []
                )
            }

            data = {
                "presets": []
            }

            if old_preset["preset_name"]:
                data["presets"].append(old_preset)

        # ================================
        # 새로운 프리셋
        # ================================
        new_preset = {
            "preset_name": preset_name,
            "targets": target_data,
            "extensions": sorted(all_extensions)
        }

        # ================================
        # 같은 이름이면 덮어쓰기
        # ================================
        replaced = False

        for index, preset in enumerate(data["presets"]):

            if preset.get("preset_name") == preset_name:

                data["presets"][index] = new_preset
                replaced = True
                break

        # 같은 이름이 없으면 새로 추가
        if not replaced:
            data["presets"].append(new_preset)

        # ================================
        # JSON 저장
        # ================================
        with open(
            file_path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=4
            )

        # ================================
        # 입력창 닫기
        # ================================
        self.hide_preset_input()

        QMessageBox.information(
            self,
            "프리셋 저장",
            f"'{preset_name}' 프리셋이 저장되었습니다."
        )

    # ================================
    # 프리셋 불러오기
    # ================================
    def load_preset(self):
        """저장된 프리셋 중 하나를 선택해서 테이블에 불러온다."""
        file_path = PRESET_PATH
        if not os.path.exists(file_path):
            QMessageBox.information(self, "프리셋 불러오기", "저장된 프리셋이 없습니다.")
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            QMessageBox.critical(self, "프리셋 불러오기 오류", f"프리셋 파일을 읽을 수 없습니다.\n{e}")
            return

        presets = data.get("presets", [])
        if not presets and data.get("preset_name"):
            presets = [data]
        if not presets:
            QMessageBox.information(self, "프리셋 불러오기", "저장된 프리셋이 없습니다.")
            return

        preset_names = [preset.get("preset_name", "이름 없음") for preset in presets]
        preset_name, ok = QInputDialog.getItem(
            self, "프리셋 불러오기", "불러올 프리셋을 선택하세요:",
            preset_names, 0, False
        )
        if not ok or not preset_name:
            return

        selected_preset = next(
            (p for p in presets if p.get("preset_name") == preset_name), None
        )
        if not selected_preset:
            return

        self.table.setRowCount(0)
        header = self.table.horizontalHeader()
        if isinstance(header, CheckBoxHeader):
            header.checked = False
            header.viewport().update()

        targets = selected_preset.get("targets", []) or selected_preset.get("folders", [])
        for target in targets:
            selected_path = target.get("path", "")
            
            if selected_path and os.path.exists(selected_path):
                self.add_table_item(selected_path, False)
                if self.core:
                    self.core.registry.add_managed_path(selected_path)

        extensions = selected_preset.get("extensions", [])
        print(f"프리셋 '{preset_name}' 불러오기 완료")
        print("저장된 확장자:", extensions)

    # ================================
    # 메인화면으로 이동
    # ================================
    def load_paths_from_db(self):
        """저장된 관리 경로를 현재 UI에 복원합니다."""
        try:
            for path in self.core.registry.get_managed_paths():
                if os.path.exists(path):
                    self.add_table_item(path, True)
        except Exception as exc:
            print(f"관리 경로 로드 실패: {exc}")

    def start_tagging(self):
        """체크된 기존 경로에 대해 백그라운드 AI 태깅을 실행합니다."""
        if not self.core:
            QMessageBox.warning(self, "태그 부착", "코어 시스템이 초기화되지 않았습니다.")
            return
        paths = []
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            checkbox = widget.findChild(QCheckBox) if widget else None
            path_item = self.table.item(row, 3)
            if checkbox and checkbox.isChecked() and path_item:
                paths.append(path_item.text().strip())
        if not paths:
            QMessageBox.warning(self, "태그 부착", "태그를 부착할 경로를 선택해주세요.")
            return

        self._tagging_worker = FolderScanAndTagWorker(paths, self.core)
        self._tagging_worker.progress.connect(self.on_tagging_progress)
        self._tagging_worker.finished.connect(self.on_tagging_finished)
        self._tagging_worker.error.connect(self.on_tagging_error)
        self._tagging_worker.start()
        QMessageBox.information(self, "태그 부착", "AI 태깅을 시작합니다.")

    def on_tagging_progress(self, message):
        print(message)

    def on_tagging_finished(self):
        QMessageBox.information(self, "태그 부착", "AI 태깅이 완료되었습니다.")

    def on_tagging_error(self, message):
        QMessageBox.critical(self, "태그 부착 오류", message)

    def go_search(self):
        self.stacked_widget.setCurrentIndex(1)

    # ================================
    # 테이블 행 추가
    # ================================
    def add_table_item(self, selected_path, checked=False):
        selected_path = os.path.abspath(selected_path)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 3)
            if item and os.path.normcase(item.text()) == os.path.normcase(selected_path):
                return
        path_name = os.path.basename(selected_path)
        row = self.table.rowCount()
        self.table.insertRow(row)

        checkbox = QCheckBox()
        checkbox.setChecked(checked)
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.addWidget(checkbox)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table.setCellWidget(row, 0, widget)

        numItem = QTableWidgetItem(str(row + 1))
        numItem.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 1, numItem)
        self.table.setItem(row, 2, QTableWidgetItem(path_name))
        self.table.setItem(row, 3, QTableWidgetItem(selected_path))
    # ================================
    # 체크된 경로 삭제
    # ================================
    def delete_selected_paths(self):
        """테이블에서 체크박스가 선택된 행들을 삭제하고 번호를 재정렬한다."""
        rows_to_delete = []

        # 체크된 행 수집
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox and checkbox.isChecked():
                    rows_to_delete.append(row)

        if not rows_to_delete:
            QMessageBox.information(
                self,
                "경로 삭제",
                "삭제할 항목을 선택해주세요."
            )
            return

        # 역순으로 행 삭제 (인덱스 꼬임 방지)
        for row in reversed(rows_to_delete):
            path_item = self.table.item(row, 3)
            if self.core and path_item:
                self.core.registry.remove_managed_path(path_item.text())
            self.table.removeRow(row)

        # '번호' 컬럼(index 1) 재정렬 및 헤더 체크 상태 초기화
        for row in range(self.table.rowCount()):
            num_item = self.table.item(row, 1)
            if num_item:
                num_item.setText(str(row + 1))

        # 전체 선택 헤더 체크 해제
        header = self.table.horizontalHeader()
        if isinstance(header, CheckBoxHeader):
            header.checked = False
            header.viewport().update()
#===================settings_view.py===================================


