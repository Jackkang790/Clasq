from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.utils.core import ClasqCore
from src.utils.workers import OrganizeApplyWorker


class OrganizePreviewApplyBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.input_dir = Path(self.tmp) / "test_input"
        self.output_dir = Path(self.tmp) / "organized"
        self.input_dir.mkdir()
        self.output_dir.mkdir()
        self.db_path = str(Path(self.tmp) / "files.db")
        self.core = ClasqCore(db_path=self.db_path)

        tagged_names = {
            "report.docx": "문서",
            "memo.txt": "문서",
            "cat.jpg": "이미지",
            "screenshot.png": "이미지",
        }
        self.sources = {}
        for name, tag in tagged_names.items():
            path = self.input_dir / name
            path.write_bytes(f"fixture:{name}".encode("utf-8"))
            result = self.core.registry.save_file_result(str(path), {
                "@TYPE": "@DB",
                "status": "SUCCESS",
                "metadata": {
                    "display_name": path.stem,
                    "tags": [tag],
                    "ai_comment": "fixture",
                },
            })
            self.assertTrue(result["success"])
            self.sources[name] = path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _groups_and_preview(self):
        files = self.core.get_files_for_organize()
        groups = self.core.group_files_by_tags(files)
        preview = self.core.build_organize_preview(groups, str(self.output_dir))
        return files, groups, preview

    def _move_plan(self, files, preview):
        by_path = {item["file_path"]: item for item in files}
        return [
            {
                "file_id": by_path[item["source_path"]]["id"],
                "file_path": item["source_path"],
                "target_path": item["target_path"],
                "file_name": item["file_name"],
            }
            for item in preview if not item["has_conflict"]
        ]

    def _db_paths(self):
        with sqlite3.connect(self.db_path) as conn:
            return {row[0] for row in conn.execute("SELECT file_path FROM files")}

    def test_path_scan_only_registers_targets_without_moving_files(self):
        scanned = self.core.scan_directory_files(str(self.input_dir))
        self.assertEqual({item["file_name"] for item in scanned}, set(self.sources))
        self.assertTrue(all(path.is_file() for path in self.sources.values()))
        self.assertEqual(list(self.output_dir.iterdir()), [])

    def test_preview_matches_future_tag_folders_without_filesystem_or_db_changes(self):
        before_paths = self._db_paths()
        _, _, preview = self._groups_and_preview()

        self.assertEqual({item["tag"] for item in preview}, {"문서", "이미지"})
        self.assertEqual(len(preview), 4)
        self.assertTrue(all(path.is_file() for path in self.sources.values()))
        self.assertFalse((self.output_dir / "문서").exists())
        self.assertFalse((self.output_dir / "이미지").exists())
        self.assertEqual(self._db_paths(), before_paths)

    def test_plan_completed_displays_and_stores_future_preview(self):
        from src.ui.views.organize_view import OrganizeView

        view = OrganizeView(self.core)
        plan = {
            "scanned": [str(path) for path in self.sources.values()],
            "counts": {"scanned": 4, "new": 0},
            "text_index": {"indexed": 0},
        }
        with patch(
            "src.ui.views.organize_view.QFileDialog.getExistingDirectory",
            return_value=str(self.output_dir),
        ):
            view._on_plan_completed(plan)

        self.assertEqual(view._preview_base_path, str(self.output_dir))
        self.assertEqual(len(view._preview_move_plan), 4)
        self.assertEqual(
            {Path(item["target_path"]).parent.name for item in view._preview_move_plan},
            {"문서", "이미지"},
        )
        self.assertTrue(view._grouped_screen.confirm_btn.isEnabled())
        self.assertFalse((self.output_dir / "문서").exists())
        self.assertFalse((self.output_dir / "이미지").exists())
        self.assertTrue(all(path.is_file() for path in self.sources.values()))
        view.deleteLater()
        self.app.processEvents()

    def test_apply_creates_previewed_folders_moves_files_and_updates_db(self):
        files, _, preview = self._groups_and_preview()
        completed = []
        worker = OrganizeApplyWorker(self._move_plan(files, preview), self.db_path)
        worker.completed.connect(completed.append)
        worker.run()

        self.assertEqual(len(completed), 1)
        self.assertEqual(len(completed[0]["moved"]), 4)
        self.assertEqual(completed[0]["failed"], [])
        expected = {item["target_path"] for item in preview}
        self.assertEqual(self._db_paths(), expected)
        self.assertTrue(all(Path(path).is_file() for path in expected))
        self.assertTrue(all(not path.exists() for path in self.sources.values()))

    def test_collision_is_excluded_and_partial_failure_rolls_back_db_and_files(self):
        conflict = self.output_dir / "문서" / "report.docx"
        conflict.parent.mkdir()
        conflict.write_bytes(b"existing-do-not-overwrite")
        files, _, preview = self._groups_and_preview()
        conflict_item = next(item for item in preview if item["file_name"] == "report.docx")
        self.assertTrue(conflict_item["has_conflict"])

        move_plan = self._move_plan(files, preview)
        before_paths = self._db_paths()
        real_move = shutil.move
        move_calls = 0

        def fail_second_move(source, destination, *args, **kwargs):
            nonlocal move_calls
            move_calls += 1
            if move_calls == 2:
                raise OSError("simulated move failure")
            return real_move(source, destination, *args, **kwargs)

        completed = []
        worker = OrganizeApplyWorker(move_plan, self.db_path)
        worker.completed.connect(completed.append)
        with patch("shutil.move", side_effect=fail_second_move):
            worker.run()

        self.assertEqual(len(completed), 1)
        self.assertEqual(len(completed[0]["failed"]), 1)
        self.assertTrue(any(item["success"] for item in completed[0]["rolled_back"]))
        self.assertEqual(self._db_paths(), before_paths)
        self.assertTrue(all(path.is_file() for path in self.sources.values()))
        self.assertEqual(conflict.read_bytes(), b"existing-do-not-overwrite")


if __name__ == "__main__":
    unittest.main()
