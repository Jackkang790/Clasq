from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


class SavedView(QWidget):
    """SQLite에 저장된 실제 AI 분석 결과를 표시하고 메타데이터를 수정하는 화면."""

    HEADERS = ["파일명", "표시명", "경로", "카테고리", "태그", "설명", "크기", "수정 시각"]

    def __init__(self, core=None, parent=None):
        super().__init__(parent)
        self.core = core
        self._row_ids = []
        self.init_ui()
        self.reload_files()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        notice = QLabel("저장된 AI 분석 결과입니다. 표시명·태그·설명만 수정할 수 있습니다.")
        notice.setObjectName("noticeBanner")
        notice.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(notice)

        header_layout = QHBoxLayout()
        title = QLabel("태그 저장 목록")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1A1A1A;")
        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self.reload_files)
        self.edit_btn = QPushButton("수정사항 저장")
        self.edit_btn.clicked.connect(self.on_save_changes)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(refresh)
        header_layout.addWidget(self.edit_btn)
        main_layout.addLayout(header_layout)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for index in (3, 4, 5, 6, 7):
            self.table.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeToContents)
        self.table.setStyleSheet("QTableWidget { background: white; border: 1px solid #EBEBEE; border-radius: 10px; }")
        main_layout.addWidget(self.table, 1)

    def reload_files(self):
        self.table.setRowCount(0)
        self._row_ids = []
        if not self.core:
            return
        try:
            for row, item in enumerate(self.core.get_saved_files()):
                self.table.insertRow(row)
                values = [item["file_name"], item["display_name"], item["file_path"], item["category"],
                          item["tags"], item["description"], f'{item["file_size"]:,} B', item["updated_at"]]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(str(value))
                    if column in (0, 2, 3, 6, 7):
                        cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(row, column, cell)
                self._row_ids.append(item["id"])
        except Exception as exc:
            QMessageBox.critical(self, "저장 목록 오류", f"저장된 파일을 불러오지 못했습니다.\n{exc}")

    def showEvent(self, event):
        super().showEvent(event)
        self.reload_files()

    def on_save_changes(self):
        if not self.core:
            QMessageBox.warning(self, "저장", "코어 시스템이 초기화되지 않았습니다.")
            return
        errors = []
        for row, file_id in enumerate(self._row_ids):
            result = self.core.update_saved_file(
                file_id, self.table.item(row, 1).text(), self.table.item(row, 4).text(), self.table.item(row, 5).text(),
            )
            if not result.get("success"):
                errors.append(result.get("message", f"행 {row + 1} 저장 실패"))
        if errors:
            QMessageBox.warning(self, "일부 저장 실패", "\n".join(errors[:5]))
        else:
            QMessageBox.information(self, "완료", f"{len(self._row_ids)}개 항목을 저장했습니다.")
            self.reload_files()
