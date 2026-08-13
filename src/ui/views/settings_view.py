import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, 
    QGroupBox, QPushButton, QLabel, QTableWidget, 
    QTableWidgetItem, QCheckBox, QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Qt, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QIcon, QPainter, QColor


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


class SettingsView(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_layout()

    def init_layout(self):
        # 영역나누기
        mainLayout = QVBoxLayout()      # 전체 영역
        header = QHBoxLayout()          # 상단 영역

        option = QGroupBox('')          # 버튼 그룹
        optionlayout = QHBoxLayout()    # 버튼 배치용 레이아웃

        middlelayout = QHBoxLayout()    # 중간 영역

        # 테이블 영역
        tablebox = QGroupBox('')        # 테이블 그룹
        tablebox.setObjectName("tablebox")
        tablelayout = QVBoxLayout()      # 테이블 배치용 레이아웃
        btnlayout = QHBoxLayout()        # 버튼 배치용 레이아웃

        # 상단요소
        backbtn = QPushButton(' 메인화면')
        # 아이콘 경로는 메인 실행 위치(프로젝트 루트) 기준으로 설정
        icon_path = os.path.join("assets", "icons", "back2.svg")
        if os.path.exists(icon_path):
            backbtn.setIcon(QIcon(icon_path))
        backbtn.setObjectName("backbtn")

        title = QLabel('파일경로 지정')
        title.setObjectName("title")

        # 중간영역 버튼들
        savebtn = QPushButton('프리셋 저장하기')
        reloadbtn = QPushButton('프리셋 불러오기')
        togleName = QLabel('자동')
        toggle = ToggleSwitch()
        
        togleName.setObjectName("toglename")
        clearbtn = QPushButton('정리하기')

        addRoot = QPushButton('경로추가')
        addRoot.setObjectName("addRoot")
        add_icon_path = os.path.join("assets", "icons", "add.svg")
        if os.path.exists(add_icon_path):
            addRoot.setIcon(QIcon(add_icon_path))

        # 테이블영역
        table = QTableWidget()
        table.setRowCount(3)
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["선택", "번호", "폴더이름", "파일경로"])
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        
        for row in range(3):
            checkbox = QCheckBox()

            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.addWidget(checkbox)
            layout.setAlignment(Qt.AlignCenter)
            layout.setContentsMargins(0, 0, 0, 0)

            table.setCellWidget(row, 0, widget)
            numItem = QTableWidgetItem(str(row + 1))
            numItem.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, 1, numItem)
            table.setItem(row, 2, QTableWidgetItem("폴더이름"))
            table.setItem(row, 3, QTableWidgetItem("파일경로"))
        
        tableheader = table.horizontalHeader()
        table.setColumnWidth(0, 50)
        table.setColumnWidth(1, 60)
        tableheader.setSectionResizeMode(0, QHeaderView.Fixed)
        tableheader.setSectionResizeMode(1, QHeaderView.Fixed)
        tableheader.setSectionResizeMode(2, QHeaderView.Stretch)
        tableheader.setSectionResizeMode(3, QHeaderView.Stretch)

        # 헤더요소 배치
        header.addWidget(title)
        header.addStretch()
        header.addWidget(backbtn)

        # 버튼 그룹 배치
        optionlayout.addWidget(savebtn)
        optionlayout.addWidget(reloadbtn)
        optionlayout.addStretch()
        optionlayout.addWidget(togleName)
        optionlayout.addWidget(toggle)
        optionlayout.addWidget(clearbtn)

        option.setLayout(optionlayout)

        # 테이블 배치
        btnlayout.addStretch()
        btnlayout.addWidget(addRoot)
        tablelayout.addLayout(btnlayout)
        tablelayout.addWidget(table)
        tablebox.setLayout(tablelayout)

        # 메인 레이아웃
        mainLayout.addLayout(header)
        mainLayout.addWidget(option)
        mainLayout.addLayout(middlelayout)
        mainLayout.addWidget(tablebox, 1)

        # 메인 레이아웃 적용
        self.setLayout(mainLayout)