import os
import json
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
    def __init__(self, stacked_widget, parent=None):
        super().__init__(parent)
        self.stacked_widget = stacked_widget
        self.setObjectName("settingsView")
        self.init_layout()
        self.init_ui()

    def init_ui(self):
        self.setStyleSheet("""
            QWidget#settingsView {
                background-color: #F7F9FC;
                color: #37352F;
                font-size: 10pt;
            }
            QLabel#title {
                padding: 2px;
                font-size: 18pt;
            }
            QLabel#toglename {
                background: transparent;
            }
            QPushButton#addRoot {
                padding: 8px 16px;
                border-radius: 8px;
                background-color: #4F84E8;
                color: white;
                border: none;
            }
            QPushButton#addRoot:hover { background-color: #3F73DC; }
            QPushButton#addRoot:pressed { background-color: #436FC2; }

            QPushButton#savebtn, QPushButton#reloadbtn, QPushButton#clearbtn {
                padding: 8px 16px;
                border-radius: 8px;
                background-color: #E5E7EB;
                color: #2F3437;
                border: 1px solid #D9D9D6;
            }
            QPushButton#savebtn:hover,
            QPushButton#reloadbtn:hover,
            QPushButton#clearbtn:hover { background-color: #D9DCE1; }
            QPushButton#savebtn:pressed,
            QPushButton#reloadbtn:pressed,
            QPushButton#clearbtn:pressed { background-color: #CDD1D8; }

            QPushButton#backbtn { background: transparent; border: none; }

            QGroupBox {
                background-color: #FFFFFF;
                border: 1px solid #E5E5E5;
                border-radius: 10px;
            }
            QGroupBox#tablebox {
                background: #F0F8FF;
                border: 1px solid #D6EAF8;
                padding: 5px;
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
            QCheckBox { spacing: 6px; }
            QCheckBox::indicator {
                width: 16px; height: 16px;
                border: 1px solid #B8C2CC;
                border-radius: 4px;
                background-color: #FFFFFF;
            }
            QCheckBox::indicator:hover { border: 1px solid #5B8DEF; }
            QCheckBox::indicator:checked {
                background-color: #4F84E8;
                border: 1px solid #4F84E8;
                image: url(assets/styles/icons/check.svg);
            }
            QTableWidget QWidget { background: transparent; }
        """)

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
        icon_path = os.path.join("assets", "styles", "icons", "home.svg")
        backbtn.setIcon(QIcon(icon_path))
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
                    self.add_table_item(os.path.abspath(file_path), True)
        elif action == folder_action:
            folder_path = QFileDialog.getExistingDirectory(self, "폴더 선택")
            if folder_path:
                self.add_table_item(os.path.abspath(folder_path), True)

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
        file_path = os.path.join(
            __import__('src.utils.app_paths', fromlist=['app_base_dir']).app_base_dir(),
            "assets",
            "preset.json"
        )

        os.makedirs(
            os.path.dirname(file_path),
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
        file_path = os.path.join(__import__('src.utils.app_paths', fromlist=['app_base_dir']).app_base_dir(), "assets", "preset.json")
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
            
            if selected_path:
                self.add_table_item(selected_path, False)

        extensions = selected_preset.get("extensions", [])
        print(f"프리셋 '{preset_name}' 불러오기 완료")
        print("저장된 확장자:", extensions)

    # ================================
    # 메인화면으로 이동
    # ================================
    def go_search(self):
        self.stacked_widget.setCurrentIndex(1)

    # ================================
    # 테이블 행 추가
    # ================================
    def add_table_item(self, selected_path, checked=False):
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