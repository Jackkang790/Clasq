import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidgetSelectionRange

from src.ui.views.saved_view import SavedView


class _Registry:
    def __init__(self):
        self.deleted = []

    def delete_record(self, file_id):
        self.deleted.append(file_id)
        return True


class _Core:
    def __init__(self):
        self.registry = _Registry()
        self.rows = [
            {"id": 1, "file_name": "one.txt", "tags": "a", "file_path": "C:/one.txt"},
            {"id": 2, "file_name": "two.txt", "tags": "b", "file_path": "C:/two.txt"},
            {"id": 3, "file_name": "three.txt", "tags": "c", "file_path": "C:/three.txt"},
        ]

    def get_all_files(self):
        return [row for row in self.rows if row["id"] not in self.registry.deleted]


class SavedBulkDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_multiple_selected_rows_are_deleted_with_one_confirmation(self):
        core = _Core()
        view = SavedView(core=core)
        view.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 0, 3), True)
        view.table.setRangeSelected(QTableWidgetSelectionRange(2, 0, 2, 3), True)
        self.assertEqual(view.selected_records(), [(1, "one.txt"), (3, "three.txt")])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             patch.object(QMessageBox, "information"):
            view.on_delete_selected()
        self.assertEqual(core.registry.deleted, [1, 3])
        self.assertEqual(view.table.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
