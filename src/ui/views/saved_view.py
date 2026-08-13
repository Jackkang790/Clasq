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

        # 1. 상단 백그라운드 안내 배너
        notice_banner = QLabel("! Ctrl + 1을 누르면 백그라운드에서 실행됩니다")
        notice_banner.setObjectName("noticeBanner")
        notice_banner.setAlignment(Qt.AlignCenter)
        notice_banner.setStyleSheet("""
            QLabel#noticeBanner {
                background-color: #222222;
                color: #FFFFFF;
                font-size: 14px;
                font-weight: bold;
                padding: 10px;
                border-radius: 4px;
            }
        """)
        main_layout.addWidget(notice_banner)

        # 2. 헤더 영역 (타이틀 + 수정하기 버튼)
        header_layout = QHBoxLayout()

        title_label = QLabel("태그 저장 목록")
        title_label.setStyleSheet("""
            font-size: 24px;
            font-weight: bold;
            color: #222222;
        """)

        self.edit_btn = QPushButton("수정하기")
        self.edit_btn.setFixedSize(110, 38)
        self.edit_btn.setStyleSheet("""
            QPushButton {
                background-color: #5B6B8C;
                color: white;
                font-size: 15px;
                font-weight: bold;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #4A5875;
            }
            QPushButton:pressed {
                background-color: #3A465E;
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

        # 인라인 편집 설정 (더블 클릭 시 셀 수정 가능)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked)

        # 임시 더미 데이터 세팅
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

        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #FFFFFF;
                gridline-color: #E0E0E0;
                border: 1px solid #D0D0D0;
                font-size: 14px;
            }
            QHeaderView::section {
                background-color: #A6A6A6;
                color: #FFFFFF;
                font-size: 15px;
                font-weight: bold;
                padding: 8px;
                border: 1px solid #8C8C8C;
            }
            QTableWidget::item {
                padding: 6px;
            }
            QTableWidget::item:focus {
                background-color: #E8F0FE;
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
        # SQLite DB 연동 및 업데이트 처리 영역
        updated_data = []
        for row in range(self.table.rowCount()):
            filename = self.table.item(row, 0).text() if self.table.item(row, 0) else ""
            tag = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
            path = self.table.item(row, 2).text() if self.table.item(row, 2) else ""
            updated_data.append((filename, tag, path))

        print(f"[DB 저장 완료] 업데이트된 목록: {updated_data}")
        QMessageBox.information(self, "완료", "성공적으로 DB에 반영되었습니다.")