import os
import shutil
import sqlite3
import unittest
from pathlib import Path

from src.utils.core import build_incremental_analysis_plan, scan_directory_files
from src.utils.db_manager import FileRegistryManager
from src.utils.workers import FolderAnalysisPlanWorker, FolderScanAndTagWorker


SUCCESS = {
    "response_type": "FILE_ORGANIZE",
    "payload": {"data": {"status": "SUCCESS", "error": None}},
}


class FakeProcessor:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first
        self.worker = None
        self.stop_after_first = False

    def process_file_upload(self, file_path):
        self.calls.append(file_path)
        if self.stop_after_first and len(self.calls) == 1:
            self.worker.request_stop()
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("one file failed")
        return SUCCESS


class IncrementalOrganizeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = os.path.abspath("tests/fixtures/incremental_runtime")
        os.makedirs(self.tempdir, exist_ok=True)
        self.db_path = os.path.abspath(".test_incremental_organize.db")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.registry = FileRegistryManager(db_path=self.db_path, duplicate_policy="keep")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.remove(path)

    def _file(self, name, content):
        path = Path(self.tempdir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    def _save_analyzed(self, path):
        result = self.registry.save_file_result(path, {
            "metadata": {"ai_comment": "analyzed", "tags": ["test"]}
        })
        self.assertTrue(result["success"])

    def test_existing_analyzed_file_is_skipped_and_changed_file_is_pending(self):
        path = self._file("one.txt", "before")
        self._save_analyzed(path)
        plan = build_incremental_analysis_plan([path], self.db_path)
        self.assertEqual(plan["counts"]["already_analyzed"], 1)
        processor = FakeProcessor()
        worker = FolderScanAndTagWorker(plan["pending"], processor)
        worker.run()
        self.assertEqual(processor.calls, [])

        Path(path).write_text("after", encoding="utf-8")
        changed = build_incremental_analysis_plan([path], self.db_path)
        self.assertEqual(changed["counts"]["changed"], 1)
        self.assertEqual(changed["counts"]["pending"], 1)

    def test_same_content_reuses_metadata_without_moving_file(self):
        source = self._file("source.txt", "identical")
        copied = self._file("nested/copy.txt", "identical")
        self._save_analyzed(source)
        plan = build_incremental_analysis_plan([source, copied], self.db_path)
        worker = FolderAnalysisPlanWorker([], db_path=self.db_path, file_paths=[])
        worker._register_same_content(plan)

        self.assertTrue(os.path.exists(copied))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(copied), "_duplicates")))
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT ai_comment, file_hash FROM files WHERE file_path = ?", (copied,)
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "analyzed")
        self.assertEqual(plan["counts"]["same_content"], 1)
        processor = FakeProcessor()
        FolderScanAndTagWorker(plan["pending"], processor).run()
        self.assertEqual(processor.calls, [])

    def test_default_batch_is_50_and_remaining_is_exact(self):
        processor = FakeProcessor()
        stats = []
        worker = FolderScanAndTagWorker([f"file-{i}" for i in range(60)], processor)
        worker.completed.connect(stats.append)
        worker.run()
        self.assertEqual(len(processor.calls), 50)
        self.assertEqual(stats[0]["remaining"], 10)

    def test_stop_finishes_current_file_and_does_not_start_next(self):
        processor = FakeProcessor()
        worker = FolderScanAndTagWorker(["one", "two", "three"], processor)
        processor.worker = worker
        processor.stop_after_first = True
        stopped = []
        worker.stopped.connect(stopped.append)
        worker.run()
        self.assertEqual([os.path.basename(path) for path in processor.calls], ["one"])
        self.assertTrue(stopped[0]["stopped"])

    def test_individual_failure_continues_to_next_file(self):
        processor = FakeProcessor(fail_first=True)
        completed = []
        worker = FolderScanAndTagWorker(["bad", "good"], processor)
        worker.completed.connect(completed.append)
        worker.run()
        self.assertEqual([os.path.basename(path) for path in processor.calls], ["bad", "good"])
        self.assertEqual(completed[0]["failed"], 1)
        self.assertEqual(completed[0]["success"], 1)
        self.assertEqual(completed[0]["remaining"], 1)

    def test_default_excluded_directories_are_not_scanned(self):
        visible = self._file("visible.txt", "ok")
        for directory in (".git", ".idea", "node_modules", ".venv", "venv", "__pycache__"):
            self._file(f"{directory}/hidden.txt", "hidden")
        self.assertEqual(scan_directory_files(self.tempdir), [visible])

    def test_new_file_hashes_once_and_unchanged_pending_file_uses_stat_cache(self):
        path = self._file("cached-new.txt", "new")
        calls = []

        def counting_hash(file_path):
            calls.append(file_path)
            return FileRegistryManager.compute_file_hash(file_path)

        first = build_incremental_analysis_plan([path], self.db_path, counting_hash)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["performance"]["sha256_calculated"], 1)
        connection = sqlite3.connect(self.db_path)
        try:
            stored = connection.execute(
                "SELECT file_mtime_ns FROM file_fingerprint_cache WHERE file_path = ?", (path,)
            ).fetchone()[0]
            visible_rows = connection.execute("SELECT count(*) FROM files").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(stored, os.stat(path).st_mtime_ns)
        self.assertEqual(visible_rows, 0)

        calls.clear()
        second = build_incremental_analysis_plan([path], self.db_path, counting_hash)
        self.assertEqual(calls, [])
        self.assertEqual(second["counts"]["new"], 1)
        self.assertEqual(second["counts"]["pending"], 1)
        self.assertEqual(second["performance"]["stat_only_skipped"], 1)

    def test_timestamp_only_change_rehashes_then_backfills_without_qwen(self):
        path = self._file("timestamp.txt", "same-content")
        self._save_analyzed(path)
        before = os.stat(path).st_mtime_ns
        os.utime(path, ns=(before + 2_000_000_000, before + 2_000_000_000))
        calls = []
        plan = build_incremental_analysis_plan(
            [path], self.db_path,
            lambda p: calls.append(p) or FileRegistryManager.compute_file_hash(p),
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(plan["counts"]["already_analyzed"], 1)
        self.assertEqual(plan["counts"]["pending"], 0)
        connection = sqlite3.connect(self.db_path)
        try:
            stored = connection.execute(
                "SELECT file_mtime_ns FROM files WHERE file_path = ?", (path,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(stored, os.stat(path).st_mtime_ns)

    def test_legacy_null_mtime_hashes_once_then_uses_stat_only(self):
        path = self._file("legacy.txt", "legacy")
        self._save_analyzed(path)
        connection = sqlite3.connect(self.db_path)
        connection.execute("UPDATE files SET file_mtime_ns = NULL WHERE file_path = ?", (path,))
        connection.commit()
        connection.close()
        calls = []
        hasher = lambda p: calls.append(p) or FileRegistryManager.compute_file_hash(p)
        first = build_incremental_analysis_plan([path], self.db_path, hasher)
        self.assertEqual(first["performance"]["sha256_calculated"], 1)
        calls.clear()
        second = build_incremental_analysis_plan([path], self.db_path, hasher)
        self.assertEqual(calls, [])
        self.assertEqual(second["counts"]["already_analyzed"], 1)

    def test_schema_migration_preserves_legacy_data(self):
        legacy_db = os.path.abspath(".test_legacy_mtime.db")
        if os.path.exists(legacy_db):
            os.remove(legacy_db)
        connection = sqlite3.connect(legacy_db)
        connection.execute(
            "CREATE TABLE files (id INTEGER PRIMARY KEY, file_name TEXT, file_path TEXT UNIQUE, "
            "ai_comment TEXT, category TEXT, file_hash TEXT, file_size INTEGER, "
            "created_at TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO files(file_name,file_path,ai_comment,category) VALUES('old','old','meta','#tag')"
        )
        connection.commit()
        connection.close()
        try:
            FileRegistryManager(db_path=legacy_db)
            connection = sqlite3.connect(legacy_db)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(files)")}
            row = connection.execute("SELECT file_name, ai_comment FROM files").fetchone()
            connection.close()
            self.assertIn("file_mtime_ns", columns)
            self.assertEqual(row, ("old", "meta"))
        finally:
            if os.path.exists(legacy_db):
                os.remove(legacy_db)


if __name__ == "__main__":
    unittest.main()
