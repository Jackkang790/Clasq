import sys
import os
import time
from datetime import datetime
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLineEdit,
    QLabel,
    QStackedWidget,
    QScrollArea,
    QFrame,
    QGraphicsDropShadowEffect,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QMessageBox,
    QDialog,
    QInputDialog,
)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, QTimer, QThread, Signal
from PySide6.QtGui import QColor, QAction, QPalette

# pynput 모듈의 대소문자 정확히 적용 (GlobalHotKeys)
from pynput.keyboard import GlobalHotKeys


# ==============================================================================
# 5초 카운트다운 백그라운드 스레드
# ==============================================================================
class GroupCardTimerThread(QThread):
    trigger_signal = Signal()
    status_signal = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False

    def run(self):
        self.running = True
        self.status_signal.emit("⏳ 그룹 카드 실행 카운트다운 시작 (5초 후 완료)")
        
        for i in range(5, 0, -1):
            if not self.running:
                self.status_signal.emit("🔴 카운트다운이 취소되었습니다.")
                return
            self.status_signal.emit(f"⏳ {i}초 뒤 그룹 카드가 열립니다... (Ctrl+1로 백그라운드 전환 가능)")
            time.sleep(1)

        if self.running:
            self.status_signal.emit("⚡ 5초 경과! 그룹 카드 표시 완료.")
            self.trigger_signal.emit()

        self.running = False

    def stop(self):
        self.running = False


# ==============================================================================
# 글로벌 단축키(Ctrl+1) 감지 스레드 (백그라운드 최소화/복원 전용)
# ==============================================================================
class GlobalHotkeyListener(QThread):
    hotkey_triggered = Signal()

    def run(self):
        # GlobalHotKeys 대소문자 정상 적용
        with GlobalHotKeys({
            '<ctrl>+1': self.on_triggered
        }) as listener:
            listener.join()

    def on_triggered(self):
        self.hotkey_triggered.emit()


# ==============================================================================
# 커스텀 토글 스위치 위젯 (ON/OFF)
# ==============================================================================
class ToggleSwitch(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(50, 26)
        self.is_active = False

        self.bg = QFrame(self)
        self.bg.setGeometry(0, 0, 50, 26)

        self.knob = QFrame(self)
        self.knob.setGeometry(3, 3, 20, 20)

        self.anim = QPropertyAnimation(self.knob, b"pos")
        self.anim.setDuration(200)

        self.update_style()

    def mousePressEvent(self, event):
        self.is_active = not self.is_active
        end_x = 27 if self.is_active else 3
        self.anim.setStartValue(self.knob.pos())
        self.anim.setEndValue(QPoint(end_x, 3))
        self.anim.start()
        self.update_style()

    def update_style(self):
        if self.is_active:
            self.bg.setStyleSheet("background-color: #34c759; border-radius: 13px;")
            self.knob.setStyleSheet("background-color: white; border-radius: 10px;")
        else:
            self.bg.setStyleSheet("background-color: #e5e5ea; border-radius: 13px;")
            self.knob.setStyleSheet("background-color: white; border-radius: 10px;")

    def isChecked(self):
        return self.is_active


# ==============================================================================
# 순회용 다이얼로그
# ==============================================================================
class PathDisplayDialog(QDialog):
    def __init__(self, folder_name, folder_path, is_auto_mode, parent=None):
        super().__init__(parent)
        self.setWindowTitle("폴더 경로 확인")
        self.setFixedSize(450, 180)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        info_label = QLabel(f"📂 폴더명: {folder_name}")
        info_label.setStyleSheet("font-size: 15px; font-weight: bold;")

        path_label = QLabel(f"경로:\n{folder_path}")
        path_label.setWordWrap(True)
        path_label.setStyleSheet("font-size: 13px; color: #555555;")

        layout.addWidget(info_label)
        layout.addWidget(path_label)

        if not is_auto_mode:
            btn_layout = QHBoxLayout()
            btn_layout.addStretch()
            ok_btn = QPushButton("확인")
            ok_btn.setFixedSize(80, 34)
            ok_btn.setStyleSheet("""
                QPushButton {
                    background-color: #007aff;
                    color: white;
                    font-weight: bold;
                    border: none;
                    border-radius: 6px;
                }
                QPushButton:hover {
                    background-color: #0056b3;
                }
            """)
            ok_btn.clicked.connect(self.accept)
            btn_layout.addWidget(ok_btn)
            layout.addLayout(btn_layout)
        else:
            QTimer.singleShot(500, self.accept)


# ==============================================================================
# 프리셋 목록 선택 다이얼로그
# ==============================================================================
class PresetLoadDialog(QDialog):
    def __init__(self, presets_dict, parent=None):
        super().__init__(parent)
        self.presets_dict = presets_dict
        self.selected_preset_name = None

        self.setWindowTitle("프리셋 불러오기")
        self.resize(500, 350)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("📋 저장된 프리셋 목록")
        title.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(title)

        self.table = QTableWidget()
        headers = ["프리셋 이름", "폴더 수", "저장 시각"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        self.load_presets_to_table()
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("취소")
        cancel_btn.setFixedSize(80, 36)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #e5e5ea;
                color: #1d1d1f;
                font-weight: bold;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #d1d1d6;
            }
        """)
        cancel_btn.clicked.connect(self.reject)

        load_btn = QPushButton("불러오기")
        load_btn.setFixedSize(90, 36)
        load_btn.setStyleSheet("""
            QPushButton {
                background-color: #007aff;
                color: white;
                font-weight: bold;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
        """)
        load_btn.clicked.connect(self.accept_selection)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(load_btn)

        layout.addLayout(btn_layout)

    def load_presets_to_table(self):
        self.table.setRowCount(len(self.presets_dict))
        for row_idx, (name, data) in enumerate(self.presets_dict.items()):
            item_name = QTableWidgetItem(name)
            item_count = QTableWidgetItem(f"{len(data['folders'])}개")
            item_count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_time = QTableWidgetItem(data["created_at"])
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.table.setItem(row_idx, 0, item_name)
            self.table.setItem(row_idx, 1, item_count)
            self.table.setItem(row_idx, 2, item_time)

    def accept_selection(self):
        selected_rows = self.table.selectedItems()
        if not selected_rows:
            QMessageBox.warning(self, "알림", "불러올 프리셋을 선택해 주세요.")
            return

        row = self.table.currentRow()
        self.selected_preset_name = self.table.item(row, 0).text()
        self.accept()


# ==============================================================================
# 1. 수집 -> 파일이동 상세 페이지
# ==============================================================================
class FileMoveDetailPage(QWidget):
    def __init__(self, on_back_callback):
        super().__init__()
        self.on_back_callback = on_back_callback
        self.folder_counter = 0
        self.presets = {}
        self.initUI()

    def initUI(self):
        self.setStyleSheet("background-color: #f9f9fb;")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(15)

        header_layout = QHBoxLayout()

        title = QLabel("📁 폴더 수집 및 자동 순회 상세 화면")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1d1d1f;")

        back_btn = QPushButton("← 이전으로 돌아가기")
        back_btn.setFixedSize(160, 42)
        back_btn.setStyleSheet("""
            QPushButton {
                background-color: #e5e5ea;
                color: #1d1d1f;
                font-size: 14px;
                font-weight: bold;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #d1d1d6;
            }
        """)
        back_btn.clicked.connect(self.on_back_callback)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(back_btn)

        main_layout.addLayout(header_layout)

        control_frame = QFrame()
        control_frame.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        control_layout = QHBoxLayout(control_frame)

        toggle_label = QLabel("0.5초 자동:")
        toggle_label.setStyleSheet("font-size: 13px; font-weight: bold; border: none; color: #333;")
        self.toggle_btn = ToggleSwitch()

        auto_btn = QPushButton("자동")
        auto_btn.setFixedSize(80, 38)
        auto_btn.setStyleSheet("""
            QPushButton {
                background-color: #34c759;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #28a745;
            }
        """)
        auto_btn.clicked.connect(self.start_auto_process)

        preset_load_btn = QPushButton("프리셋 불러오기")
        preset_load_btn.setFixedSize(130, 38)
        preset_load_btn.setStyleSheet("""
            QPushButton {
                background-color: #5856d6;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #4340d3;
            }
        """)
        preset_load_btn.clicked.connect(self.open_preset_load_dialog)

        select_folder_btn = QPushButton("폴더 선택")
        select_folder_btn.setFixedSize(100, 38)
        select_folder_btn.setStyleSheet("""
            QPushButton {
                background-color: #007aff;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
        """)
        select_folder_btn.clicked.connect(self.open_folder_dialog)

        preset_save_btn = QPushButton("프리셋 저장하기")
        preset_save_btn.setFixedSize(130, 38)
        preset_save_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff9500;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #d97f00;
            }
        """)
        preset_save_btn.clicked.connect(self.save_current_preset)

        control_layout.addWidget(toggle_label)
        control_layout.addWidget(self.toggle_btn)
        control_layout.addSpacing(10)
        control_layout.addWidget(auto_btn)
        control_layout.addWidget(preset_load_btn)
        control_layout.addStretch()
        control_layout.addWidget(select_folder_btn)
        control_layout.addWidget(preset_save_btn)

        main_layout.addWidget(control_frame)

        self.table = QTableWidget()
        headers = ["번호", "폴더 이름", "전체 폴더 경로"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)

        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 10px;
                gridline-color: #f0f0f0;
                font-size: 13px;
            }
            QHeaderView::section {
                background-color: #f5f5f7;
                color: #333333;
                font-weight: bold;
                border: none;
                border-bottom: 1px solid #e0e0e0;
                padding: 8px;
            }
            QTableWidget::item:selected {
                background-color: #007aff;
                color: white;
            }
        """)

        main_layout.addWidget(self.table)

    def open_folder_dialog(self):
        folder_path = QFileDialog.getExistingDirectory(self, "폴더 선택", "", QFileDialog.Option.ShowDirsOnly)
        if folder_path:
            self.folder_counter += 1
            folder_name = os.path.basename(folder_path) or folder_path

            row_position = self.table.rowCount()
            self.table.insertRow(row_position)

            item_num = QTableWidgetItem(str(self.folder_counter))
            item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_name = QTableWidgetItem(folder_name)
            item_path = QTableWidgetItem(folder_path)

            self.table.setItem(row_position, 0, item_num)
            self.table.setItem(row_position, 1, item_name)
            self.table.setItem(row_position, 2, item_path)

    def save_current_preset(self):
        row_count = self.table.rowCount()
        if row_count == 0:
            QMessageBox.warning(self, "알림", "저장할 테이블 데이터가 없습니다.")
            return

        preset_name, ok = QInputDialog.getText(self, "프리셋 저장", "저장할 프리셋 이름을 입력해 주세요:")

        if ok and preset_name.strip():
            preset_name = preset_name.strip()

            folders_data = []
            for row in range(row_count):
                folder_name = self.table.item(row, 1).text()
                folder_path = self.table.item(row, 2).text()
                folders_data.append((folder_name, folder_path))

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            self.presets[preset_name] = {
                "created_at": now_str,
                "folders": folders_data,
            }

            self.table.setRowCount(0)
            self.folder_counter = 0

            QMessageBox.information(
                self, "완료", f"'{preset_name}' 프리셋이 성공적으로 저장되었으며 테이블이 클리어되었습니다."
            )

    def open_preset_load_dialog(self):
        if not self.presets:
            QMessageBox.information(self, "알림", "저장된 프리셋이 존재하지 않습니다.")
            return

        dialog = PresetLoadDialog(self.presets, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_name = dialog.selected_preset_name
            if selected_name and selected_name in self.presets:
                preset_info = self.presets[selected_name]

                self.table.setRowCount(0)
                self.folder_counter = 0

                for folder_name, folder_path in preset_info["folders"]:
                    self.folder_counter += 1
                    row_position = self.table.rowCount()
                    self.table.insertRow(row_position)

                    item_num = QTableWidgetItem(str(self.folder_counter))
                    item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                    item_name = QTableWidgetItem(folder_name)
                    item_path = QTableWidgetItem(folder_path)

                    self.table.setItem(row_position, 0, item_num)
                    self.table.setItem(row_position, 1, item_name)
                    self.table.setItem(row_position, 2, item_path)

                QMessageBox.information(
                    self, "완료", f"'{selected_name}' 프리셋 데이터를 성공적으로 불러왔습니다."
                )

    def start_auto_process(self):
        row_count = self.table.rowCount()
        if row_count == 0:
            QMessageBox.warning(self, "알림", "테이블에 수집된 폴더 경로가 없습니다.")
            return

        is_auto_mode = self.toggle_btn.isChecked()

        for row in range(row_count):
            self.table.selectRow(row)

            folder_name = self.table.item(row, 1).text()
            folder_path = self.table.item(row, 2).text()

            dialog = PathDisplayDialog(folder_name, folder_path, is_auto_mode, self)
            dialog.exec()

        QMessageBox.information(self, "완료", "모든 폴더의 순회가 완료되었습니다.")


# ==============================================================================
# 2. 메인 UI 내부 카드 및 메뉴 페이지들
# ==============================================================================
class CardWidget(QFrame):
    def __init__(self, title, description):
        super().__init__()
        self.setStyleSheet("""
            CardWidget {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 12px;
                padding: 16px;
            }
            CardWidget:hover {
                border: 1px solid #007aff;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 15))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1d1d1f;")

        desc_label = QLabel(description)
        desc_label.setStyleSheet("font-size: 13px; color: #86868b;")
        desc_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(desc_label)


class SearchPage(QWidget):
    def __init__(self):
        super().__init__()
        self.is_searched = False
        self.initUI()

    def initUI(self):
        self.setMinimumSize(600, 500)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(20, 20, 20, 20)
        self.cards_layout.setSpacing(12)
        self.cards_layout.addStretch()
        self.scroll_area.setWidget(self.cards_container)
        self.scroll_area.hide()

        self.search_container = QWidget(self)
        self.search_container.setFixedSize(450, 120)

        container_layout = QVBoxLayout(self.search_container)
        container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_label = QLabel("무엇을 찾고 계신가요?")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #333333;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        search_box_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("검색어를 입력하세요...")
        self.search_input.setFixedHeight(42)
        self.search_input.setStyleSheet("""
            QLineEdit {
                border: 2px solid #dcdcdc;
                border-radius: 8px;
                padding: 0 12px;
                font-size: 14px;
                background-color: #ffffff;
            }
            QLineEdit:focus {
                border: 2px solid #007aff;
            }
        """)
        self.search_input.returnPressed.connect(self.trigger_search)

        self.search_btn = QPushButton("검색")
        self.search_btn.setFixedHeight(42)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background-color: #007aff;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
        """)
        self.search_btn.clicked.connect(self.trigger_search)

        search_box_layout.addWidget(self.search_input)
        search_box_layout.addWidget(self.search_btn)

        container_layout.addWidget(self.title_label)
        container_layout.addLayout(search_box_layout)

        self.anim = QPropertyAnimation(self.search_container, b"pos")
        self.anim.setDuration(400)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self.on_animation_finished)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()

        if not self.is_searched:
            cx = (w - self.search_container.width()) // 2
            cy = (h - self.search_container.height()) // 2
            self.search_container.move(cx, cy)
        else:
            cx = (w - self.search_container.width()) // 2
            self.search_container.move(cx, 10)
            self.scroll_area.setGeometry(
                10,
                self.search_container.height() + 20,
                w - 20,
                h - self.search_container.height() - 30,
            )

    def trigger_search(self):
        text = self.search_input.text().strip()
        if text and not self.is_searched:
            self.is_searched = True

            start_pos = self.search_container.pos()
            end_x = (self.width() - self.search_container.width()) // 2
            end_pos = QPoint(end_x, 10)

            self.anim.setStartValue(start_pos)
            self.anim.setEndValue(end_pos)
            self.anim.start()

            self.load_cards(text)

        elif text and self.is_searched:
            self.load_cards(text)

    def on_animation_finished(self):
        w, h = self.width(), self.height()
        self.scroll_area.setGeometry(
            10,
            self.search_container.height() + 20,
            w - 20,
            h - self.search_container.height() - 30,
        )
        self.scroll_area.show()

    def load_cards(self, keyword):
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i in range(1, 6):
            card = CardWidget(
                f"'{keyword}' 관련 검색 결과 #{i}",
                f"선택하신 검색어 '{keyword}'에 대한 상세 결과 내용입니다. 이곳에 카드 상세 데이터가 표시됩니다.",
            )
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)


# ==============================================================================
# 2번 메뉴 페이지
# ==============================================================================
class Menu2Page(QWidget):
    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window
        self.initUI()

    def initUI(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        top_layout = QHBoxLayout()

        title_label = QLabel("📦 2번 메뉴: 데이터 현황")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #1d1d1f;")

        self.top_action_btn = QPushButton("그룹 카드로 보기 (5초 타이머)")
        self.top_action_btn.setFixedSize(200, 38)
        self.top_action_btn.setStyleSheet("""
            QPushButton {
                background-color: #34c759;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #28a745;
            }
        """)
        # 버튼 클릭 시 5초 백그라운드 타이머 스레드 실행
        self.top_action_btn.clicked.connect(self.on_btn_clicked)

        top_layout.addWidget(title_label)
        top_layout.addStretch()
        top_layout.addWidget(self.top_action_btn)

        main_layout.addLayout(top_layout)

        self.table = QTableWidget()
        headers = ["ID", "제품명", "카테고리", "수량", "가격", "등록일"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)

        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 10px;
                gridline-color: #f0f0f0;
                font-size: 13px;
            }
            QHeaderView::section {
                background-color: #f5f5f7;
                color: #333333;
                font-weight: bold;
                border: none;
                border-bottom: 1px solid #e0e0e0;
                padding: 8px;
            }
            QTableWidget::item:selected {
                background-color: #007aff;
                color: white;
            }
        """)

        self.load_dummy_data()
        main_layout.addWidget(self.table)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(20)

        self.scroll_area.setWidget(self.cards_container)
        self.scroll_area.hide()

        main_layout.addWidget(self.scroll_area)

    def load_dummy_data(self):
        dummy_data = [
            ["P-001", "무선 마우스", "전자기기", "150", "29,000원", "2024-05-01"],
            ["P-002", "기계식 키보드", "전자기기", "85", "129,000원", "2024-05-02"],
            ["P-003", "27인치 모니터", "디스플레이", "40", "340,000원", "2024-05-03"],
            ["P-004", "USB-C 허브", "액세서리", "210", "45,000원", "2024-05-04"],
            ["P-005", "노트북 거치대", "액세서리", "95", "32,000원", "2024-05-05"],
        ]

        self.table.setRowCount(len(dummy_data))
        for row_idx, row_data in enumerate(dummy_data):
            for col_idx, value in enumerate(row_data):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_idx, col_idx, item)

    def on_btn_clicked(self):
        """[그룹 카드로 보기] 클릭 시 5초 타이머 실행 안내 및 백그라운드 스레드 작동"""
        if self.main_window:
            self.main_window.start_group_card_timer()

    def render_cards(self):
        """실제 5초 지난 후 UI를 카드로 그리는 함수"""
        self.table.hide()
        self.scroll_area.show()

        while self.cards_layout.count() > 0:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        groups_data = [
            {
                "title": "1번 그룹: 포도 🍇",
                "items": [
                    ("캠벨", "당도가 높고 친숙한 대표적인 포도 품종입니다."),
                    ("샤인머스켓", "씨가 없고 껍질째 먹는 프리미엄 망고포도입니다."),
                    ("솜사탕포도", "솜사탕처럼 진한 달콤함을 자랑하는 과일입니다."),
                    ("거봉", "알이 굵고 즙이 풍부한 최고급 포도입니다.")
                ]
            },
            {
                "title": "2번 그룹: 사과 🍎",
                "items": [
                    ("빨간사과(부사)", "아삭한 식감과 과즙이 풍부한 만생종 사과입니다."),
                    ("청사과(아오리)", "상큼한 맛이 일품인 여름 대표 사과입니다."),
                    ("홍옥", "새콤달콤한 맛이 강하고 색이 붉은 사과입니다."),
                    ("시나노 골드", "황금빛을 띠는 고당도 아삭한 사과입니다.")
                ]
            },
            {
                "title": "3번 그룹: 감귤류 🍊",
                "items": [
                    ("한라봉", "우뚝 솟은 봉우리가 특징인 대표 만감류입니다."),
                    ("천혜향", "향이 천 리를 간다고 하여 붙여진 이름입니다."),
                    ("레드향", "붉은빛을 띠며 껍질이 잘 벗겨지는 신품종입니다."),
                    ("온주밀감", "겨울철 손쉽게 즐기는 대표 국민 과일입니다.")
                ]
            },
            {
                "title": "4번 그룹: 복숭아 🍑",
                "items": [
                    ("백도", "과육이 흰색이고 부드러우며 당도가 높은 복숭아입니다."),
                    ("황도", "노란 과육과 쫀득한 식감이 매력적인 복숭아입니다."),
                    ("천도복숭아", "털이 없고 매끈하며 새콤달콤한 맛입니다."),
                    ("신비복숭아", "겉은 천도 같으나 속은 백도처럼 달콤합니다.")
                ]
            },
            {
                "title": "5번 그룹: 베리류 🍓",
                "items": [
                    ("딸기", "새콤달콤함과 향긋함이 인상적인 과채류입니다."),
                    ("블루베리", "안토시아닌이 풍부한 슈퍼푸드 베리입니다."),
                    ("라즈베리", "톡톡 터지는 식감과 독특한 향을 가진 산딸기입니다."),
                    ("블랙베리", "진한 검은색을 띠는 건강한 복분자류 과일입니다.")
                ]
            },
        ]

        for group in groups_data:
            group_box = QFrame()
            group_box.setStyleSheet("""
                QFrame {
                    background-color: #ffffff;
                    border: 1px solid #e0e0e0;
                    border-radius: 12px;
                    padding: 16px;
                }
            """)
            group_layout = QVBoxLayout(group_box)
            group_layout.setSpacing(12)

            group_title = QLabel(group["title"])
            group_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #007aff; border: none;")
            group_layout.addWidget(group_title)

            cards_hbox = QHBoxLayout()
            cards_hbox.setSpacing(10)

            for item_name, item_desc in group["items"]:
                card = CardWidget(item_name, item_desc)
                cards_hbox.addWidget(card)

            group_layout.addLayout(cards_hbox)
            self.cards_layout.addWidget(group_box)

        self.cards_layout.addStretch()


class DatabasePage(QWidget):
    def __init__(self):
        super().__init__()
        self.db_data = [
            ["1", "홍길동", "gildong@example.com", "관리자", "2024-01-15", "활성"],
            ["2", "김철수", "chulsoo@example.com", "사용자", "2024-02-01", "활성"],
            ["3", "이영희", "younghee@example.com", "편집자", "2024-02-20", "대기"],
            ["4", "박민수", "minsu@example.com", "사용자", "2024-03-05", "비활성"],
            ["5", "정수진", "sujin@example.com", "사용자", "2024-03-12", "활성"],
        ]
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QHBoxLayout()
        title = QLabel("📊 데이터베이스 사용자 목록")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1d1d1f;")

        self.edit_btn = QPushButton("수정하기")
        self.edit_btn.setFixedSize(100, 36)
        self.edit_btn.setStyleSheet("""
            QPushButton {
                background-color: #007aff;
                color: white;
                font-weight: bold;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
        """)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.edit_btn)

        layout.addLayout(header_layout)

        self.table = QTableWidget()
        headers = ["ID", "이름", "이메일", "역할", "가입일자", "상태"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)

        self.load_data_to_table()
        layout.addWidget(self.table)

    def load_data_to_table(self):
        self.table.setRowCount(len(self.db_data))
        for row_idx, row_data in enumerate(self.db_data):
            for col_idx, value in enumerate(row_data):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_idx, col_idx, item)


# ==============================================================================
# 3. 메인 창 (백그라운드 최소화/복원 제어 및 타이머 스레드 관리)
# ==============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PySide6 커스텀 UI")
        self.resize(900, 650)

        self.root_stack = QStackedWidget()
        self.setCentralWidget(self.root_stack)

        self.main_app_widget = QWidget()
        self.init_main_app_ui()

        self.detail_page = FileMoveDetailPage(on_back_callback=self.go_back_to_main)

        self.root_stack.addWidget(self.main_app_widget)
        self.root_stack.addWidget(self.detail_page)

        # 5초 타이머 스레드 초기화
        self.timer_thread = GroupCardTimerThread()
        self.timer_thread.trigger_signal.connect(self.on_timer_finished)
        self.timer_thread.status_signal.connect(self.update_status_label)

        # 글로벌 단축키(Ctrl+1) 리스너 (창 최소화/복원 토글)
        self.hotkey_listener = GlobalHotkeyListener()
        self.hotkey_listener.hotkey_triggered.connect(self.toggle_minimize_restore)
        self.hotkey_listener.start()

    def init_main_app_ui(self):
        app_layout = QVBoxLayout(self.main_app_widget)
        app_layout.setContentsMargins(0, 0, 0, 0)
        app_layout.setSpacing(0)

        menu_bar = self.menuBar()
        collect_menu = menu_bar.addMenu("수집")
        file_move_action = QAction("파일이동", self)
        file_move_action.triggered.connect(self.open_detail_page)
        collect_menu.addAction(file_move_action)

        # 상태 안내 바
        self.status_bar_frame = QFrame()
        self.status_bar_frame.setStyleSheet("background-color: #1c1c1e; padding: 4px;")
        status_layout = QHBoxLayout(self.status_bar_frame)
        status_layout.setContentsMargins(15, 5, 15, 5)

        self.status_label = QLabel("상태: [Ctrl + 1] 단축키를 눌러 언제든지 창을 백그라운드로 최소화/복원할 수 있습니다.")
        self.status_label.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")

        status_layout.addWidget(self.status_label)
        app_layout.addWidget(self.status_bar_frame)

        body_widget = QWidget()
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet("background-color: #f5f5f7; border-right: 1px solid #e0e0e0;")

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        sidebar_layout.setContentsMargins(10, 20, 10, 20)

        self.btn_menu1 = QPushButton("1번 메뉴 (검색)")
        self.btn_menu2 = QPushButton("2번 메뉴 (버튼)")
        self.btn_menu3 = QPushButton("3번 메뉴 (DB)")

        menu_style = """
            QPushButton {
                text-align: left;
                padding: 12px;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #e8e8ed;
            }
        """
        self.btn_menu1.setStyleSheet(menu_style)
        self.btn_menu2.setStyleSheet(menu_style)
        self.btn_menu3.setStyleSheet(menu_style)

        sidebar_layout.addWidget(self.btn_menu1)
        sidebar_layout.addWidget(self.btn_menu2)
        sidebar_layout.addWidget(self.btn_menu3)

        self.content_stack = QStackedWidget()
        self.page1 = SearchPage()
        self.page2 = Menu2Page(main_window=self)
        self.page3 = DatabasePage()

        self.content_stack.addWidget(self.page1)
        self.content_stack.addWidget(self.page2)
        self.content_stack.addWidget(self.page3)

        self.btn_menu1.clicked.connect(lambda: self.content_stack.setCurrentIndex(0))
        self.btn_menu2.clicked.connect(lambda: self.content_stack.setCurrentIndex(1))
        self.btn_menu3.clicked.connect(lambda: self.content_stack.setCurrentIndex(2))

        body_layout.addWidget(sidebar)
        body_layout.addWidget(self.content_stack)

        app_layout.addWidget(body_widget)

    def start_group_card_timer(self):
        """[그룹 카드로 보기] 클릭 시 실행되는 5초 백그라운드 타이머 스레드"""
        if not self.timer_thread.isRunning():
            self.timer_thread.start()

    def toggle_minimize_restore(self):
        """[Ctrl + 1] 입력 시 창 최소화 (백그라운드 전환) 또는 복원"""
        if self.isMinimized():
            self.showNormal()
            self.raise_()
            self.activateWindow()
        else:
            self.showMinimized()

    def update_status_label(self, message):
        """하단 바 상태 변경"""
        self.status_label.setText(f"상태: {message}")

    def on_timer_finished(self):
        """5초 카운트다운 완료 시 처리 동작"""
        # 1. 2번 메뉴 카드화면 표시
        self.root_stack.setCurrentIndex(0)
        self.content_stack.setCurrentIndex(1)
        self.page2.render_cards()

        # 2. 백그라운드에 숨겨져 있었다면 화면 최상단으로 강제 복원
        self.showNormal()
        self.raise_()
        self.activateWindow()

        # 3. 5초 경과 완료 알림창 팝업 생성
        QMessageBox.information(
            self,
            "작업 완료",
            "🎉 5초가 지나 그룹 카드 데이터가 성공적으로 표시되었습니다!"
        )

    def open_detail_page(self):
        self.root_stack.setCurrentIndex(1)

    def go_back_to_main(self):
        self.root_stack.setCurrentIndex(0)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # -------------------------------------------------------------
    # 1. Qt 애플리케이션 스타일을 Fusion으로 고정 (OS별 기본 테마 영향 제거)
    app.setStyle("Fusion")
    
    # 2. 라이트 모드 팔레트 강제 적용
    light_palette = QPalette()
    light_palette.setColor(QPalette.Window, QColor(249, 249, 251))
    light_palette.setColor(QPalette.WindowText, QColor(29, 29, 31))
    light_palette.setColor(QPalette.Base, QColor(255, 255, 255))
    light_palette.setColor(QPalette.AlternateBase, QColor(245, 245, 247))
    light_palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
    light_palette.setColor(QPalette.ToolTipText, QColor(29, 29, 31))
    light_palette.setColor(QPalette.Text, QColor(29, 29, 31))
    light_palette.setColor(QPalette.Button, QColor(255, 255, 255))
    light_palette.setColor(QPalette.ButtonText, QColor(29, 29, 31))
    light_palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    light_palette.setColor(QPalette.Highlight, QColor(0, 122, 255))
    light_palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    
    app.setPalette(light_palette)
    # -------------------------------------------------------------

    window = MainWindow()
    window.show()
    sys.exit(app.exec())