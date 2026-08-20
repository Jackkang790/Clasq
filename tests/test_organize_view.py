import os
import sqlite3
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.ui.views.organize_view import OrganizeView, _FileTableScreen, _GroupedScreen
from src.utils.core import get_files_for_organize, scan_directory_files


class OrganizeViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_initial_screens_have_no_mock_rows_or_groups(self):
        table = _FileTableScreen()
        grouped = _GroupedScreen()
        self.assertEqual(table.table.rowCount(), 0)
        self.assertEqual(grouped.group_layout.count(), 1)
        self.assertFalse(grouped.status_banner.isVisible())

    def test_recursive_scan_returns_only_supported_files(self):
        root = Path("tests/fixtures/organize").resolve()
        files = scan_directory_files(str(root))
        self.assertEqual(len(files), 3)
        self.assertTrue(all(path.endswith(".txt") for path in files))

    def test_large_file_list_renders_only_one_200_row_page(self):
        view = OrganizeView(db_path=".missing-organize-pagination.db", main_processor=object())
        view._scanned_files = [str(Path(f"fake/{index}.txt").resolve()) for index in range(21641)]
        view._show_scanned_files()
        self.assertEqual(view._table_screen.table.rowCount(), 200)
        self.assertIn("21,641", view._table_screen.page_label.text())
        self.assertIn("109", view._table_screen.page_label.text())

    def test_batch_completion_reenables_controls_before_plan_refresh(self):
        view = OrganizeView(db_path=".missing-organize-completion.db", main_processor=object())
        view._set_tagging_busy(True)
        refresh_calls = []
        view._start_analysis_plan = lambda disable_controls=True: refresh_calls.append(disable_controls)
        stats = {"processed": 50, "success": 50, "failed": 0}
        view._on_tagging_completed(stats)
        self.assertTrue(view._table_screen.add_path_btn.isEnabled())
        self.assertTrue(view._table_screen.auto_btn.isEnabled())
        self.assertEqual(refresh_calls, [False])
        self.assertIn("갱신 중", view._table_screen.status_banner.label.text())

    def test_plan_statistics_use_worker_counts_not_a_zero_placeholder(self):
        counts = {
            "scanned": 21641, "already_analyzed": 56, "same_content": 0,
            "new": 21585, "changed": 0, "pending": 21585,
        }
        initial = OrganizeView._format_plan_statistics(counts, batch_count=50)
        self.assertIn("총 21,641개", initial)
        self.assertIn("기존 분석 완료 56개", initial)
        self.assertIn("신규 21,585개", initial)
        self.assertIn("분석 필요 21,585개", initial)

        refreshed_counts = dict(counts, already_analyzed=106, same_content=481,
                                new=21054, pending=21054)
        refreshed = OrganizeView._format_plan_statistics(refreshed_counts, refreshed=True)
        self.assertIn("분석 완료 587개", refreshed)
        self.assertIn("동일 내용 재사용 481개", refreshed)
        self.assertIn("분석 필요 21,054개", refreshed)

    def test_db_filter_and_group_count_use_real_rows(self):
        db_path = ".test_organize_core.db"
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                "CREATE TABLE files (id INTEGER PRIMARY KEY, file_name TEXT, file_path TEXT, ai_comment TEXT, category TEXT)"
            )
            rows = [
                ("a.txt", str(Path("a.txt").resolve()), "태그: #업무 / 코멘트: a", "#업무"),
                ("b.txt", str(Path("b.txt").resolve()), "태그: #업무 / 코멘트: b", "#업무"),
                ("c.txt", str(Path("c.txt").resolve()), "#분석실패 / 코멘트: error", "#일반"),
            ]
            connection.executemany(
                "INSERT INTO files(file_name,file_path,ai_comment,category) VALUES(?,?,?,?)", rows
            )
            connection.commit()
        finally:
            connection.close()
        try:
            analyzed = get_files_for_organize(db_path)
            self.assertEqual(len(analyzed), 2)
            view = OrganizeView(db_path=db_path, main_processor=object())
            view._scanned_files = [row[1] for row in rows]
            view._analysis_completed = True
            view._build_groups_from_db()
            self.assertEqual(view._grouped_screen.group_layout.count() - 1, 1)
        finally:
            Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
