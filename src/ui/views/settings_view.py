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
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QFileDialog
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QStyle, QStyleOptionButton
from src.utils.workers import FolderScanAndTagWorker, FolderAnalysisPlanWorker
from src.ui.widgets.progress_dialog import TaskProgressDialog
from src.utils.app_paths import assets_dir
from src.ai.hardware_detector import HardwareDetector
from src.utils.diagnostic_bundle import (
    DiagnosticExportError,
    default_bundle_filename,
    export_diagnostic_bundle,
)

ASSETS_DIR = Path(assets_dir())
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
# 메인 설정 뷰
# ================================
class SettingsView(QWidget):
    def __init__(self, stacked_widget, core=None, refresh_manager=None, server_manager=None, parent=None):
        super().__init__(parent)
        self.stacked_widget = stacked_widget
        self.core = core
        self.refresh_manager = refresh_manager
        self.server_manager = server_manager
        self._tagging_worker = None
        self._tagging_dialog = None
        self._analysis_worker = None
        self._analysis_dialog = None
        self._index_btn = None
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
        QPushButton#savebtn, QPushButton#reloadbtn, QPushButton#clearbtn,
        QPushButton#indexbtn {
            padding: 8px 16px;
            border-radius: 8px;
            background-color: #FFFFFF;
            color: #2D3436;
            font-weight: bold;
            border: 1px solid #EBEBEE;
        }
        QPushButton#savebtn:hover,
        QPushButton#reloadbtn:hover,
        QPushButton#clearbtn:hover,
        QPushButton#indexbtn:hover {
            background-color: #F0EDFE;
            color: #6C5CE7;
            border-color: #D6CEFC;
        }
        QPushButton#savebtn:pressed,
        QPushButton#reloadbtn:pressed,
        QPushButton#clearbtn:pressed,
        QPushButton#indexbtn:pressed {
            background-color: #E0D9FC;
            color: #5B4BC4;
        }
        QPushButton#indexbtn:disabled {
            background-color: #F5F5F5;
            color: #AAAAAA;
            border-color: #EBEBEE;
        }


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

        clearbtn = QPushButton('태그부착')
        clearbtn.setObjectName("clearbtn")
        clearbtn.clicked.connect(self.start_tagging)

        indexbtn = QPushButton('색인 갱신')
        indexbtn.setObjectName("indexbtn")
        indexbtn.clicked.connect(self.start_folder_analysis)
        self._index_btn = indexbtn

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

        optionlayout.addWidget(savebtn)
        optionlayout.addWidget(reloadbtn)
        optionlayout.addStretch()
        optionlayout.addWidget(clearbtn)
        optionlayout.addWidget(indexbtn)
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
        diagnostics_layout = QHBoxLayout()
        diagnostics_note = QLabel("진단 로그와 비민감 시스템 정보만 로컬 ZIP으로 저장합니다.")
        diagnostics_btn = QPushButton("진단 정보 내보내기")
        diagnostics_btn.setObjectName("savebtn")
        diagnostics_btn.clicked.connect(self.export_diagnostics)
        diagnostics_layout.addWidget(diagnostics_note)
        diagnostics_layout.addStretch()
        diagnostics_layout.addWidget(diagnostics_btn)
        mainLayout.addLayout(diagnostics_layout)
        self.setLayout(mainLayout)

    def export_diagnostics(self):
        answer = QMessageBox.question(
            self,
            "진단 정보 내보내기",
            "진단 로그와 비민감 시스템 정보가 포함됩니다.\n"
            "사용자 문서, 데이터베이스, 모델 원본은 포함되지 않습니다.\n\n계속하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "진단 정보 저장",
            default_bundle_filename(),
            "ZIP archive (*.zip)",
        )
        if not destination:
            return
        if not destination.lower().endswith(".zip"):
            destination += ".zip"
        try:
            result = export_diagnostic_bundle(
                destination,
                server_manager=self.server_manager,
                hardware_detector=HardwareDetector().detect,
                overwrite=True,
            )
        except (DiagnosticExportError, OSError):
            QMessageBox.warning(self, "진단 정보 내보내기", "진단 파일을 만들지 못했습니다.")
            return
        QMessageBox.information(
            self,
            "진단 정보 내보내기",
            f"진단 파일을 저장했습니다.\n\n파일: {result.path.name}\nSHA-256: {result.sha256}",
        )

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
                        self._refresh_database_views()
        elif action == folder_action:
            folder_path = QFileDialog.getExistingDirectory(self, "폴더 선택")
            if folder_path:
                selected_path = os.path.abspath(folder_path)
                self.add_table_item(selected_path, True)
                if self.core:
                    self.core.registry.add_managed_path(selected_path)
                    self._refresh_database_views()

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
                dirs[:] = [d for d in dirs if d not in {"__pycache__", self.core.registry.duplicates_dir_name}]
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

        if self._tagging_worker and self._tagging_worker.isRunning():
            QMessageBox.information(self, "태그 부착", "이미 태깅 작업이 진행 중입니다.")
            return

        self._tagging_dialog = TaskProgressDialog(
            "파일 태깅 중", "파일을 분석하고 있습니다...", parent=self, unit="파일",
        )

        self._tagging_worker = FolderScanAndTagWorker(paths, self.core)
        self._tagging_worker.progress.connect(self.on_tagging_progress)
        self._tagging_worker.fileProgress.connect(self.on_tagging_file_progress)
        self._tagging_worker.finished.connect(self.on_tagging_finished)
        self._tagging_worker.error.connect(self.on_tagging_error)
        self._tagging_worker.start()
        self._tagging_dialog.show()

    def on_tagging_progress(self, message):
        print(message)

    def on_tagging_file_progress(self, current, total, file_name):
        """worker가 보낸 실제 처리 개수로 Progress Dialog를 갱신합니다."""
        if self._tagging_dialog:
            self._tagging_dialog.update_progress(current, total, file_name)

    def _close_tagging_dialog(self):
        """작업이 끝나면 Progress Dialog를 자동으로 닫습니다."""
        if self._tagging_dialog:
            self._tagging_dialog.close()
            self._tagging_dialog = None

    def on_tagging_finished(self, summary=None):
        summary = summary or {}
        self._close_tagging_dialog()
        self._refresh_database_views()
        QMessageBox.information(
            self, "태그 부착",
            f"AI 태깅 완료: 성공 {summary.get('success', 0)}개, 실패 {len(summary.get('failed', []))}개",
        )

    def on_tagging_error(self, message):
        self._close_tagging_dialog()
        QMessageBox.critical(self, "태그 부착 오류", message)

    def _refresh_database_views(self):
        if self.refresh_manager:
            self.refresh_manager.refresh()

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
        self._refresh_database_views()

    # ================================
    # 색인 갱신 (FolderAnalysisPlanWorker)
    # ================================
    def _get_checked_paths(self):
        """체크박스가 선택된 행의 경로 목록을 반환한다."""
        paths = []
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            checkbox = widget.findChild(QCheckBox) if widget else None
            path_item = self.table.item(row, 3)
            if checkbox and checkbox.isChecked() and path_item:
                paths.append(path_item.text().strip())
        return paths

    def start_folder_analysis(self):
        """체크된 경로를 대상으로 증분 분석 계획 + 텍스트 색인 + 검색 snapshot 갱신을 실행한다.

        파일을 이동·삭제·이름변경·덮어쓰기하지 않는다.
        """
        if self._analysis_worker and self._analysis_worker.isRunning():
            QMessageBox.information(self, "색인 갱신", "이미 색인 갱신 작업이 진행 중입니다.")
            return

        paths = self._get_checked_paths()
        if not paths:
            QMessageBox.warning(self, "색인 갱신", "색인을 갱신할 경로를 선택해주세요.")
            return

        db_path = self.core.db_path if self.core else "file_manager.db"

        if self._index_btn:
            self._index_btn.setEnabled(False)

        self._analysis_dialog = TaskProgressDialog(
            "색인 갱신 중", "파일을 검색하고 있습니다...", parent=self, unit="파일",
        )

        self._analysis_worker = FolderAnalysisPlanWorker(paths, db_path=db_path)
        self._analysis_worker.progress.connect(self._on_analysis_progress)
        self._analysis_worker.completed.connect(self._on_analysis_completed)
        self._analysis_worker.error.connect(self._on_analysis_error)
        self._analysis_worker.start()
        self._analysis_dialog.show()

    def _on_analysis_progress(self, message):
        if self._analysis_dialog:
            self._analysis_dialog.setLabelText(message)

    def _close_analysis_dialog(self):
        if self._analysis_dialog:
            self._analysis_dialog.close()
            self._analysis_dialog = None
        if self._index_btn:
            self._index_btn.setEnabled(True)

    def _on_analysis_completed(self, plan):
        self._close_analysis_dialog()
        counts = plan.get("counts", {})
        text_idx = plan.get("text_index", {})
        snap = plan.get("search_snapshot", {})

        scanned = counts.get("scanned", 0)
        already = counts.get("already_analyzed", 0)
        new_files = counts.get("new", 0)
        changed = counts.get("changed", 0)
        pending = counts.get("pending", 0)
        errors = counts.get("errors", 0)
        txt_indexed = text_idx.get("indexed", 0)
        snap_rows = snap.get("rows", 0)

        msg = (
            f"색인 갱신 완료\n\n"
            f"스캔 파일: {scanned:,}개\n"
            f"  · 기분석(변경 없음): {already:,}개\n"
            f"  · 신규: {new_files:,}개\n"
            f"  · 변경: {changed:,}개\n"
            f"  · 분석 필요: {pending:,}개\n"
        )
        if errors:
            msg += f"  · 오류: {errors:,}개\n"
        msg += f"\n텍스트 색인 갱신: {txt_indexed:,}개\n"
        msg += f"검색 인덱스 레코드: {snap_rows:,}개"

        QMessageBox.information(self, "색인 갱신", msg)
        self._refresh_database_views()

    def _on_analysis_error(self, message):
        self._close_analysis_dialog()
        safe_msg = message.split("\n")[0][:200] if message else "알 수 없는 오류"
        QMessageBox.critical(self, "색인 갱신 오류", safe_msg)
