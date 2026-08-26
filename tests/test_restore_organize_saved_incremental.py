from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.utils.core import ClasqCore, build_incremental_analysis_plan
from src.utils.workers import estimate_analysis_eta


class RestoreOrganizeSavedIncrementalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "input"
        self.root.mkdir()
        self.db_path = str(Path(self.tmp) / "files.db")
        self.core = ClasqCore(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _file(self, name, content=None):
        path = self.root / name
        path.write_text(content or name, encoding="utf-8")
        return path

    def _save_tagged(self, path, tag="문서"):
        result = self.core.registry.save_file_result(str(path), {
            "status": "SUCCESS",
            "metadata": {
                "display_name": path.stem,
                "tags": [tag],
                "ai_comment": "tagged",
            },
        })
        self.assertTrue(result["success"])

    def test_declined_path_tagging_still_creates_saved_recovery_records(self):
        from src.ui.views.organize_view import OrganizeView
        from src.ui.views.saved_view import SavedView

        paths = [self._file("one.txt"), self._file("two.txt")]
        plan = build_incremental_analysis_plan([str(path) for path in paths], self.db_path)
        view = OrganizeView(self.core)
        view._inventory_context = "path_add"
        with patch("src.ui.views.organize_view.QMessageBox.question", return_value=0):
            view._on_inventory_completed(plan)

        records = self.core.get_all_files()
        self.assertEqual(len(records), 2)
        self.assertTrue(all(not record["tags"] for record in records))
        self.assertEqual(view._table_screen.table.rowCount(), 2)
        saved = SavedView(core=self.core)
        self.assertEqual(saved.table.rowCount(), 2)
        self.assertEqual(set(saved._untagged_file_paths()), {str(path) for path in paths})
        self.assertTrue(all(path.is_file() for path in paths))
        saved.deleteLater()
        view.deleteLater()

    def test_path_add_yes_targets_only_pending_files(self):
        from PySide6.QtWidgets import QMessageBox
        from src.ui.views.organize_view import OrganizeView

        tagged = self._file("tagged.txt")
        new = self._file("new.txt")
        self._save_tagged(tagged)
        plan = build_incremental_analysis_plan([str(tagged), str(new)], self.db_path)
        view = OrganizeView(self.core)
        view._inventory_context = "path_add"
        with patch("src.ui.views.organize_view.QMessageBox.question", return_value=QMessageBox.Yes), \
             patch.object(view, "_start_ai_tagging") as start:
            view._on_inventory_completed(plan)

        start.assert_called_once_with([str(new)], context="path_add")
        view.deleteLater()

    def test_incremental_inventory_reuses_95_and_analyzes_only_5(self):
        existing = []
        for index in range(97):
            path = self._file(f"existing-{index}.txt", f"original-{index}")
            self._save_tagged(path)
            existing.append(path)
        for index in (95, 96):
            existing[index].write_text(f"changed-{index}", encoding="utf-8")
        new = [self._file(f"new-{index}.txt") for index in range(3)]

        plan = build_incremental_analysis_plan(
            [str(path) for path in [*existing, *new]], self.db_path
        )
        self.assertEqual(plan["counts"]["already_analyzed"], 95)
        self.assertEqual(plan["counts"]["changed"], 2)
        self.assertEqual(plan["counts"]["new"], 3)
        self.assertEqual(plan["counts"]["pending"], 5)
        self.assertEqual(plan["performance"]["stat_only_skipped"], 95)

    def test_valid_tag_is_reusable_even_without_ai_comment(self):
        path = self._file("manual-tag.txt")
        self._save_tagged(path)
        record = self.core.get_all_files()[0]
        self.core.registry.update_tags(record["id"], "수동태그")
        conn = self.core.registry._connect()
        try:
            conn.execute("UPDATE files SET ai_comment = '', category = '' WHERE id = ?", (record["id"],))
            conn.commit()
        finally:
            conn.close()
        plan = build_incremental_analysis_plan([str(path)], self.db_path)
        self.assertEqual(plan["counts"]["already_analyzed"], 1)
        self.assertEqual(plan["counts"]["pending"], 0)

    def test_eta_uses_observed_speed_without_exact_second_coupling(self):
        eta = estimate_analysis_eta(20, seconds_per_file=2)
        self.assertTrue(eta.startswith("약 "))
        self.assertIn("~", eta)
        self.assertTrue("초" in eta or "분" in eta)
        self.assertEqual(estimate_analysis_eta(0), "추가 분석 없음")

    def test_auto_organize_selects_destination_before_inventory(self):
        from src.ui.views.organize_view import OrganizeView

        path = self._file("ready.txt")
        self._save_tagged(path)
        view = OrganizeView(self.core)
        destination = str(Path(self.tmp) / "destination")
        with patch(
            "src.ui.views.organize_view.QFileDialog.getExistingDirectory",
            return_value=destination,
        ), patch.object(view, "_start_incremental_inventory") as start:
            view._on_auto_organize()

        self.assertEqual(view._auto_destination, destination)
        start.assert_called_once()
        self.assertEqual(start.call_args.kwargs["context"], "auto_organize")
        view.deleteLater()

    def test_auto_inventory_reuses_tagged_result_without_ai_call(self):
        from src.ui.views.organize_view import OrganizeView

        path = self._file("ready.txt")
        self._save_tagged(path)
        plan = build_incremental_analysis_plan([str(path)], self.db_path)
        view = OrganizeView(self.core)
        view._inventory_context = "auto_organize"
        view._auto_destination = str(Path(self.tmp) / "destination")
        with patch.object(view, "_start_ai_tagging") as tagging, \
             patch.object(view, "_on_plan_completed") as preview, \
             patch("src.ui.views.organize_view.QMessageBox.information"):
            view._on_inventory_completed(plan)

        tagging.assert_not_called()
        preview.assert_called_once_with(plan)
        view.deleteLater()

    def test_register_unanalyzed_file_never_moves_or_renames(self):
        path = self._file("untagged.txt")
        before = path.read_bytes()
        result = self.core.registry.register_unanalyzed_file(str(path))
        self.assertTrue(result["success"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
