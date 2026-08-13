import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, 
    QGroupBox, QPushButton, QLabel, QTableWidget, 
    QTableWidgetItem, QCheckBox, QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Qt, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QIcon, QPainter, QColor
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFileDialog
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QStyle, QStyleOptionButton

#전체 체크박스 구현
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
                16,
                16
            )

            if self.checked:
                option.state = QStyle.State_On
            else:
                option.state = QStyle.State_Off

            self.style().drawControl(
                QStyle.CE_CheckBox,
                option,
                painter
            )

    def mousePressEvent(self, event):
        index = self.logicalIndexAt(event.pos())

        if index == 0:
            self.checked = not self.checked

            if self.settings_view:
                self.settings_view.toggle_all(self.checked)

            self.viewport().update()

        else:
            super().mousePressEvent(event)

    def mousePressEvent(self, event):
        index = self.logicalIndexAt(event.pos())

        if index == 0:
            self.checked = not self.checked
            self.parent().toggle_all(self.checked)
            self.viewport().update()
        else:
            super().mousePressEvent(event)
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

    def __init__(self, stacked_widget, parent=None):
        super().__init__(parent)
        self.stacked_widget = stacked_widget
        self.setObjectName("settingsView")
        self.init_layout()
        self.init_ui()

    def init_ui(self):
        self.setStyleSheet("""
            QWidget#settingsView {
    
                background-color:#F7F9FC;
                color: #37352F;
                font-size: 10pt;
            }

            QLabel#title {
                padding:2px;
                font-size: 18pt;
    
    
            }


            QLabel#toglename {
                background: transparent;
    
            }

            QPushButton#addRoot{
                padding: 8px 16px;
                border-radius: 8px;
                background-color: #4F84E8;
                color: white;
                border: none;
                border-radius: 8px;
    
            }

            QPushButton#addRoot:hover {
                background-color: #3F73DC;
    
            }
            QPushButton#addRoot:pressed {
                background-color: #436FC2;
    
            }

            
            QPushButton#savebtn,
                QPushButton#reloadbtn,
                QPushButton#clearbtn {
                padding: 8px 16px;
                border-radius: 8px;
                background-color: #E5E7EB;
                color: #2F3437;
                border: 1px solid #D9D9D6;
    
            }
            QPushButton#savebtn:hover,
                QPushButton#reloadbtn:hover,
                QPushButton#clearbtn :hover {
                background-color:  #D9DCE1;
   
            }
            QPushButton#savebtn:pressed,
                QPushButton#reloadbtn:pressed,
                QPushButton#clearbtn:pressed {
                background-color:  #CDD1D8;
    
            }

            QPushButton#backbtn {
            
                background: transparent;
                border: none;
            }
            
            QGroupBox {
                background-color: #FFFFFF;
                border: 1px solid #E5E5E5;
                border-radius: 10px;
            }

            QGroupBox#tablebox{
                background:#F0F8FF;
                border:1px solid #D6EAF8;
                padding:5px;
            }
            QTableWidget {
                background-color: #FFFFFF;
                border: 1px solid #E5E5E5;
                gridline-color: #EEEEEE;
                border-radius: 8px;
    
    
            }
            QHeaderView::section {
                background-color: #D7E7FF;
                color: #334155;
                border: none;
                border-bottom: 1px solid #B8D1F5;
    
            }
            QCheckBox {
                spacing: 6px;
            }

            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #B8C2CC;
                border-radius: 4px;
                background-color: #FFFFFF;
            }

            QCheckBox::indicator:hover {
                border: 1px solid #5B8DEF;
            }

            QCheckBox::indicator:checked {
                background-color: #4F84E8;
                border: 1px solid #4F84E8;
                image: url(C:/vscode/orbit/orbit_view-main/assets/icons/check.svg);
            }

            QTableWidget QWidget {
                background: transparent;
            }

          
        """)
    def toggle_all(self, checked):
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)

        if widget:
            checkbox = widget.findChild(QCheckBox)

            if checkbox:
                checkbox.setChecked(checked)
    
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
        
        backbtn = QPushButton()
        icon_path = os.path.join("orbit_view-main","assets", "icons", "home.svg")
        backbtn.setIcon(QIcon(icon_path))
        backbtn.setIconSize(QSize(24, 24))
        backbtn.setObjectName("backbtn")
        backbtn.clicked.connect(self.go_search)
    
        title = QLabel('파일경로 지정')
        title.setObjectName("title")

        # 중간영역 버튼들
        savebtn = QPushButton('프리셋 저장하기')
        savebtn.setObjectName("savebtn")
        reloadbtn = QPushButton('프리셋 불러오기')
        reloadbtn.setObjectName("reloadbtn")
        togleName = QLabel('자동')
        toggle = ToggleSwitch()
        
        togleName.setObjectName("toglename")
        clearbtn = QPushButton('태그부착')
        clearbtn.setObjectName("clearbtn")

        addRoot = QPushButton('경로추가')
        addRoot.setObjectName("addRoot")
        addRoot.clicked.connect(self.add_folder)

        # 테이블영역
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["", "번호", "폴더이름", "파일경로"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)

        tableheader = CheckBoxHeader(Qt.Horizontal, self.table)
        self.table.setHorizontalHeader(tableheader)
        
        # for row in range(3):
        #     checkbox = QCheckBox()  
            

        #     widget = QWidget()
        #     layout = QHBoxLayout(widget)
        #     layout.addWidget(checkbox)
        #     layout.setAlignment(Qt.AlignCenter)
        #     layout.setContentsMargins(0, 0, 0, 0)

        #     self.table.setCellWidget(row, 0, widget)
        #     numItem = QTableWidgetItem(str(row + 1))
        #     numItem.setTextAlignment(Qt.AlignCenter)
        #     self.table.setItem(row, 1, numItem)
           
        
        tableheader = self.table.horizontalHeader()
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 60)
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
        tablelayout.addWidget(self.table)
        tablebox.setLayout(tablelayout)

        # 메인 레이아웃
        mainLayout.addLayout(header)
        mainLayout.addWidget(option)
        mainLayout.addLayout(middlelayout)
        mainLayout.addWidget(tablebox, 1)

        # 메인 레이아웃 적용
        self.setLayout(mainLayout)

    def go_search(self):
        self.stacked_widget.setCurrentIndex(1)

    def add_folder(self):
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "폴더 선택"
        )

        if not folder_path:
            return

        # 폴더 이름
        folder_name = os.path.basename(folder_path)

        # 현재 행 개수
        row = self.table.rowCount()

        # 새로운 행 추가
        self.table.insertRow(row)

        # 체크박스
        checkbox = QCheckBox()

        widget = QWidget()
        layout = QHBoxLayout(widget)

        layout.addWidget(checkbox)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table.setCellWidget(row, 0, widget)

        # 번호
        numItem = QTableWidgetItem(str(row + 1))
        numItem.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 1, numItem)

        # 폴더 이름
        self.table.setItem(
            row,
            2,
            QTableWidgetItem(folder_name)
        )

        # 폴더 경로
        self.table.setItem(
            row,
            3,
            QTableWidgetItem(folder_path)
        )

















