from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class OrganizeView(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        # 전체 메인 레이아웃 (오른쪽 메인 영역 역할)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # 1. 상단 안내문 (Ctrl + 1)
        notice = QLabel("! Ctrl + 1을 누르면 백그라운드에서 실행됩니다")
        notice.setObjectName("noticeLabel")
        notice.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(notice)

        # 2. 화면 제목
        title = QLabel("파일 자동 정리")
        title.setObjectName("titleLabel")
        main_layout.addWidget(title)

        # 3. 상단 버튼 영역 (경로 추가 / 자동 정리)
        button_layout = QHBoxLayout()
        path_button = QPushButton("경로 추가하기")
        auto_button = QPushButton("자동 정리하기")

        path_button.setObjectName("pathButton")
        auto_button.setObjectName("autoButton")

        button_layout.addWidget(path_button)
        button_layout.addWidget(auto_button)
        button_layout.addStretch()  # 버튼을 왼쪽에 정렬
        main_layout.addLayout(button_layout)

        # 4. 파일 및 태그 목록 테이블
        table = QTableWidget()
        table.setColumnCount(3)
        table.setRowCount(3)
        table.setHorizontalHeaderLabels(["파일명", "태그", "파일 경로"])

        # 임시 데이터 채우기 및 중앙 정렬
        for row in range(3):
            file_item = QTableWidgetItem(f"파일명_{row + 1}")
            tag_item = QTableWidgetItem("태그")
            path_item = QTableWidgetItem("파일 경로")

            file_item.setTextAlignment(Qt.AlignCenter)
            tag_item.setTextAlignment(Qt.AlignCenter)
            path_item.setTextAlignment(Qt.AlignCenter)

            table.setItem(row, 0, file_item)
            table.setItem(row, 1, tag_item)
            table.setItem(row, 2, path_item)

        # 테이블 헤더 컬럼 너비 자동 조절
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)

        # 테이블을 남아있는 공간에 크게 배치
        main_layout.addWidget(table, 1)