import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from PySide6.QtTest import QSignalSpy

from src.ui.components.side_bar import Sidebar
from src.ui.components.title_bar import TitleBar
from src.ui.views.organize_view import _FileTableScreen, _GroupedScreen
from src.ui.views.saved_view import SavedView
from src.ui.views.search_view import SearchView
from src.ui.views.settings_view import SettingsView
from src.ui.widgets.fileupload_view import FileUploadView


class Batch33UiActionCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_core_navigation_names_and_order_are_preserved(self):
        sidebar = Sidebar()
        self.assertEqual(
            [sidebar.btn_search.text(), sidebar.btn_organize.text(), sidebar.btn_saved.text()],
            ["검색하기", "정리하기", "저장목록"],
        )

    def test_organize_original_visible_actions_and_callbacks_remain_accessible(self):
        screen = _FileTableScreen()
        button_texts = [button.text() for button in screen.findChildren(QPushButton)]
        for required in ("프리셋 불러오기", "경로 추가", "경로 삭제", "자동정리"):
            self.assertIn(required, button_texts)
        self.assertNotIn("⋯", button_texts)
        preset_spy = QSignalSpy(screen.presetLoadRequested)
        remove_spy = QSignalSpy(screen.removePathRequested)
        buttons = {button.text(): button for button in screen.findChildren(QPushButton)}
        buttons["프리셋 불러오기"].click()
        buttons["경로 삭제"].click()
        self.assertEqual(preset_spy.count(), 1)
        self.assertEqual(remove_spy.count(), 1)

    def test_grouped_preview_has_only_edit_and_apply_actions(self):
        screen = _GroupedScreen()
        visible_action_texts = [button.text() for button in screen.findChildren(QPushButton)]
        self.assertEqual(visible_action_texts, ["수정하기", "이대로 정리하기"])

    def test_settings_management_actions_remain_accessible(self):
        settings = SettingsView(None)
        button_texts = [button.text() for button in settings.findChildren(QPushButton)]
        self.assertIn("경로추가", button_texts)
        self.assertIn("경로삭제", button_texts)
        self.assertIn("태그부착", button_texts)
        self.assertIn("프리셋 저장하기", button_texts)
        self.assertIn("프리셋 불러오기", button_texts)
        home_button = settings.findChild(QPushButton, "backbtn")
        self.assertIsNotNone(home_button)
        self.assertTrue(home_button.toolTip())

    def test_removed_search_legacy_helper_stays_absent(self):
        self.assertFalse(hasattr(SearchView, "_add_result_bubble"))

    def test_icon_only_actions_have_tooltips(self):
        title_bar = TitleBar()
        for button in (
            title_bar.settings_btn,
            title_bar.back_btn,
            title_bar.forward_btn,
            title_bar.min_btn,
            title_bar.max_btn,
            title_bar.close_btn,
        ):
            self.assertTrue(button.toolTip())

        search_input = FileUploadView()
        self.assertTrue(search_input.plus_btn.toolTip())
        self.assertTrue(search_input.send_btn.toolTip())

    def test_saved_view_multi_selection_delete_remains_accessible(self):
        class Registry:
            def __init__(self):
                self.deleted = []

            def delete_record(self, file_id):
                self.deleted.append(file_id)
                return True

        class Core:
            def __init__(self):
                self.registry = Registry()

            def get_all_files(self):
                return [
                    {"id": 1, "file_name": "one.txt", "tags": "a", "file_path": "C:/one.txt"},
                    {"id": 2, "file_name": "two.txt", "tags": "b", "file_path": "C:/two.txt"},
                ]

        core = Core()
        view = SavedView(core=core)
        selection = view.table.selectionModel()
        flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
        selection.select(view.table.model().index(0, 0), flags)
        selection.select(view.table.model().index(1, 0), flags)

        with (
            patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            patch.object(QMessageBox, "information"),
        ):
            view.on_delete_selected()

        self.assertEqual(core.registry.deleted, [1, 2])


if __name__ == "__main__":
    unittest.main()
