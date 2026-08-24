import os

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

from src.ui.widgets.progress_dialog import TaskProgressDialog
from src.utils.workers import FolderScanAndTagWorker


class SavedView(QWidget):

    def __init__(self, parent=None, core=None, refresh_manager=None):
        super().__init__(parent)
        self.core = core
        self.refresh_manager = refresh_manager
        self._original_rows = {}  # row -> (file_id, file_name, tags)
        self._tagging_worker = None
        self._tagging_dialog = None
        self.init_ui()
        self.load_data()

    def _refresh_database_views(self):
        if self.refresh_manager:
            self.refresh_manager.refresh()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        header_layout = QHBoxLayout()

        title_label = QLabel("태그 저장 목록")
        title_label.setStyleSheet("""
            font-size: 22px;
            font-weight: bold;
            color: #1A1A1A;
        """)

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

        self.delete_selected_btn = QPushButton("선택 삭제")
        self.delete_selected_btn.setFixedSize(110, 38)
        self.delete_selected_btn.setStyleSheet("""
            QPushButton { background-color: #FFFFFF; color: #E74C3C; font-size: 14px;
                          font-weight: bold; border: 1px solid #E74C3C; border-radius: 8px; }
            QPushButton:hover { background-color: #FDEDEC; }
            QPushButton:pressed { background-color: #FADBD8; }
        """)
        self.delete_selected_btn.clicked.connect(self.on_delete_selected)

        self.tag_untagged_btn = QPushButton("미태깅 전체 AI 태깅")
        self.tag_untagged_btn.setFixedSize(160, 38)
        self.tag_untagged_btn.setStyleSheet(self.edit_btn.styleSheet())
        self.tag_untagged_btn.clicked.connect(self.on_tag_all_untagged)

        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.tag_untagged_btn)
        header_layout.addWidget(self.delete_selected_btn)
        header_layout.addWidget(self.edit_btn)
        main_layout.addLayout(header_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["파일명", "태그", "파일 경로", ""])
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # 행 높이 기본값 설정 (버튼 및 텍스트 편집 상자 깨짐 방지)
        self.table.verticalHeader().setDefaultSectionSize(48)

        # 열 너비 조절 모드 설정
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        
        # 각 열의 기본 최소 너비 설정
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(3, 90)

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

            QScrollBar:horizontal {
                border: none;
                background-color: transparent;
                height: 8px;
                margin: 0px 0px 0px 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal {
                background-color: #CBD5E1;
                min-width: 30px;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: #94A3B8;
            }
            QScrollBar::handle:horizontal:pressed {
                background-color: #6C5CE7;
            }
            QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {
                border: none;
                background: none;
                width: 0px;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: none;
            }
        """)

        main_layout.addWidget(self.table, 1)

    # ------------------------------------------------------------------
    # DB -> 테이블 로드
    # ------------------------------------------------------------------
    def load_data(self):
        """core.get_all_files()로 실제 DB 데이터를 읽어와 테이블을 채운다."""
        self.table.setRowCount(0)
        self._original_rows.clear()

        if self.core is None:
            return

        try:
            files = self.core.get_all_files()
        except Exception as e:
            QMessageBox.warning(self, "조회 실패", f"DB 조회 중 오류가 발생했습니다: {e}")
            return

        self.table.setRowCount(len(files))
        for row, file_info in enumerate(files):
            file_id = file_info["id"]
            file_name = file_info["file_name"]
            tags = file_info["tags"]
            file_path = file_info["file_path"]

            item_filename = QTableWidgetItem(file_name)
            item_tag = QTableWidgetItem(tags)
            item_path = QTableWidgetItem(file_path)

            item_filename.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            item_tag.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            item_path.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            item_filename.setData(Qt.UserRole, file_id)
            item_path.setFlags(item_path.flags() & ~Qt.ItemIsEditable)

            self.table.setItem(row, 0, item_filename)
            self.table.setItem(row, 1, item_tag)
            self.table.setItem(row, 2, item_path)

            # 삭제 버튼을 정중앙에 배치하기 위한 컨테이너
            btn_container = QWidget()
            btn_layout = QHBoxLayout(btn_container)
            btn_layout.setContentsMargins(4, 0, 4, 0)
            btn_layout.setAlignment(Qt.AlignCenter)

            delete_btn = QPushButton("삭제")
            # 폭을 64px로 확대하고 고정 높이 대신 min/max 설정
            delete_btn.setFixedWidth(64)
            delete_btn.setFixedHeight(30)
            
            delete_btn.setStyleSheet("""
                QPushButton {
                    background-color: #FFFFFF;
                    color: #E74C3C;
                    font-size: 12px;
                    font-weight: bold;
                    border: 1px solid #E74C3C;
                    border-radius: 6px;
                    padding: 0px;  /* 패딩을 0으로 설정하여 내부 글자 여백 확보 */
                    margin: 0px;
                }
                QPushButton:hover {
                    background-color: #FDEDEC;
                }
                QPushButton:pressed {
                    background-color: #FADBD8;
                }
            """)
            delete_btn.clicked.connect(
                lambda checked=False, fid=file_id, fname=file_name: self.on_delete_file(fid, fname)
            )
            btn_layout.addWidget(delete_btn)

            self.table.setCellWidget(row, 3, btn_container)
            self._original_rows[row] = (file_id, file_name, tags)

    # ------------------------------------------------------------------
    # 수정하기 버튼 클릭 시 확인 팝업 및 저장 로직
    # ------------------------------------------------------------------
    def on_save_changes(self):
        if self.core is None:
            QMessageBox.warning(self, "오류", "DB에 연결되어 있지 않습니다.")
            return

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
        errors = []
        changed_count = 0

        for row in range(self.table.rowCount()):
            if row not in self._original_rows:
                continue
            file_id, old_name, old_tags = self._original_rows[row]

            item_filename = self.table.item(row, 0)
            item_tag = self.table.item(row, 1)

            new_name = item_filename.text().strip() if item_filename else old_name
            new_tags = item_tag.text().strip() if item_tag else old_tags

            # 1) 파일명이 바뀌었으면 실제 파일 이름도 함께 변경
            if new_name and new_name != old_name:
                if self.core.registry.rename_file(file_id, new_name):
                    changed_count += 1
                else:
                    errors.append(f"'{old_name}' 이름 변경 실패")

            # 2) 태그가 바뀌었으면 DB에 반영
            if new_tags != old_tags:
                if self.core.registry.update_tags(file_id, new_tags):
                    changed_count += 1
                else:
                    errors.append(f"'{new_name}' 태그 업데이트 실패")

        self.load_data()
        self._refresh_database_views()

        if errors:
            QMessageBox.warning(
                self, "일부 저장 실패",
                "다음 항목 처리 중 오류가 발생했습니다:\n" + "\n".join(errors),
            )
        else:
            QMessageBox.information(
                self, "완료", f"성공적으로 DB에 반영되었습니다. ({changed_count}건 변경)"
            )

    def on_delete_file(self, file_id, file_name):
        if self.core is None:
            QMessageBox.warning(self, "오류", "DB에 연결되어 있지 않습니다.")
            return

        reply = QMessageBox.question(
            self,
            "삭제 확인",
            f"'{file_name}' 항목을 DB 저장 목록에서 삭제하시겠습니까?\n실제 파일은 삭제되지 않고 그대로 유지됩니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if self.core.registry.delete_record(file_id):
            self.load_data()
            self._refresh_database_views()
            QMessageBox.information(
                self, "완료",
                f"'{file_name}' 항목을 DB에서 삭제했습니다. (실제 파일은 유지됨)",
            )
        else:
            QMessageBox.warning(self, "삭제 실패", f"'{file_name}' DB 레코드 삭제 중 오류가 발생했습니다.")

    def selected_records(self):
        """Return unique selected DB records in visual row order."""
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        records = []
        for row in rows:
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.UserRole) is not None:
                records.append((item.data(Qt.UserRole), item.text()))
        return records

    def on_delete_selected(self):
        """Delete selected DB records in one user-confirmed action; files remain on disk."""
        if self.core is None:
            QMessageBox.warning(self, "오류", "DB에 연결되어 있지 않습니다.")
            return
        records = self.selected_records()
        if not records:
            QMessageBox.information(self, "선택 삭제", "삭제할 항목을 하나 이상 선택해주세요.")
            return
        reply = QMessageBox.question(
            self, "선택 삭제 확인",
            f"선택한 {len(records)}개 항목을 DB 저장 목록에서 삭제하시겠습니까?\n"
            "실제 파일은 삭제되지 않고 그대로 유지됩니다.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        failed = [name for file_id, name in records if not self.core.registry.delete_record(file_id)]
        self.load_data()
        self._refresh_database_views()
        if failed:
            QMessageBox.warning(
                self, "일부 삭제 실패",
                f"성공 {len(records) - len(failed)}개, 실패 {len(failed)}개",
            )
        else:
            QMessageBox.information(self, "완료", f"선택한 {len(records)}개 항목을 DB에서 삭제했습니다.")

    def _untagged_file_paths(self):
        """Return existing saved-list files whose tag field is empty."""
        paths = []
        for row in range(self.table.rowCount()):
            tag_item = self.table.item(row, 1)
            path_item = self.table.item(row, 2)
            if path_item and not (tag_item.text().strip() if tag_item else ""):
                path = path_item.text().strip()
                if path and os.path.isfile(path):
                    paths.append(path)
        return paths

    def on_tag_all_untagged(self):
        """Explicitly AI-tag every currently untagged saved-list file."""
        if self.core is None:
            QMessageBox.warning(self, "AI 태깅", "DB에 연결되어 있지 않습니다.")
            return
        if self._tagging_worker and self._tagging_worker.isRunning():
            QMessageBox.information(self, "AI 태깅", "이미 AI 태깅 작업이 진행 중입니다.")
            return

        paths = self._untagged_file_paths()
        if not paths:
            QMessageBox.information(self, "AI 태깅", "AI 태깅이 필요한 파일이 없습니다.")
            return

        reply = QMessageBox.question(
            self,
            "미태깅 파일 AI 태깅",
            f"태그가 없는 {len(paths):,}개 파일을 AI로 분석해 태깅하시겠습니까?\n"
            "이미 태그된 파일은 변경하지 않으며, 파일 이동은 수행하지 않습니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._tagging_dialog = TaskProgressDialog(
            "미태깅 파일 AI 태깅", "AI 분석을 준비하고 있습니다...", parent=self, unit="파일",
        )
        self._tagging_worker = FolderScanAndTagWorker(paths, self.core)
        self._tagging_worker.progress.connect(self._tagging_dialog.setLabelText)
        self._tagging_worker.fileProgress.connect(self._tagging_dialog.update_progress)
        self._tagging_worker.finished.connect(self._on_bulk_tagging_finished)
        self._tagging_worker.error.connect(self._on_bulk_tagging_error)
        self._tagging_worker.start()
        self._tagging_dialog.show()

    def _close_tagging_dialog(self):
        if self._tagging_dialog:
            self._tagging_dialog.close()
            self._tagging_dialog = None

    def _on_bulk_tagging_finished(self, summary=None):
        summary = summary or {}
        self._close_tagging_dialog()
        self.load_data()
        self._refresh_database_views()
        remaining = len(self._untagged_file_paths())
        QMessageBox.information(
            self,
            "AI 태깅 완료",
            f"성공 {summary.get('success', 0):,}개, "
            f"실패 {len(summary.get('failed', [])):,}개, "
            f"남은 미태깅 {remaining:,}개",
        )

    def _on_bulk_tagging_error(self, message):
        self._close_tagging_dialog()
        QMessageBox.critical(self, "AI 태깅 오류", str(message))
