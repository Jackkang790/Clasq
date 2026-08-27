import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from src.utils.core import build_incremental_analysis_plan
from src.utils.db_manager import FileRegistryManager
from src.utils.workers import IncrementalInventoryWorker


class FileScanResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _db(self, root):
        path = str(Path(root, "scan.db"))
        FileRegistryManager(db_path=path)
        return path

    def test_2326_files_run_off_main_thread_and_event_loop_ticks(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            paths = []
            for index in range(2326):
                path = Path(root, f"f{index}.txt")
                path.write_text("x", encoding="utf-8")
                paths.append(str(path))
            worker = IncrementalInventoryWorker(file_paths=paths, db_path=self._db(root))
            loop = QEventLoop()
            ticks = []
            thread_ids = []
            plans = []
            timer = QTimer()
            timer.setInterval(5)
            timer.timeout.connect(lambda: ticks.append(time.monotonic()))
            worker.fileProgress.connect(lambda *_: thread_ids.append(threading.get_ident()))
            worker.completed.connect(plans.append)
            worker.finished.connect(loop.quit)
            timer.start()
            worker.start()
            loop.exec()
            timer.stop()
            self.assertGreater(len(ticks), 2)
            self.assertTrue(thread_ids)
            self.assertEqual(set(thread_ids), {threading.main_thread().ident})
            self.assertEqual(plans[0]["performance"]["sha256_calculated"], 0)
            self.assertEqual(plans[0]["performance"]["hash_deferred"], 2326)

    def test_2326_eager_vs_lazy_performance_metrics(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            payload = b"x" * (32 * 1024)
            paths = []
            for index in range(2326):
                path = Path(root, f"bench-{index}.txt")
                path.write_bytes(payload)
                paths.append(str(path))

            eager_db = self._db(root)
            eager_started = time.perf_counter()
            eager = build_incremental_analysis_plan(paths, eager_db, defer_hash=False)
            eager_persist_started = time.perf_counter()
            eager_registry = FileRegistryManager(db_path=eager_db)
            with eager_registry.bulk_session():
                for item in eager["new"]:
                    self.assertTrue(eager_registry.register_unanalyzed_file(item["file_path"], item)["success"])
            eager_persist_seconds = time.perf_counter() - eager_persist_started
            eager_seconds = time.perf_counter() - eager_started

            lazy_db = str(Path(root, "lazy.db"))
            FileRegistryManager(db_path=lazy_db)
            worker = IncrementalInventoryWorker(file_paths=paths, db_path=lazy_db)
            plans = []
            worker.completed.connect(plans.append)
            lazy_started = time.perf_counter()
            worker.run()
            lazy_seconds = time.perf_counter() - lazy_started
            lazy = plans[0]

            print(
                "PERF2326 "
                f"before_ui_ready={eager_seconds:.6f}s before_hash_count={eager['performance']['sha256_calculated']} "
                f"before_bytes_hashed={eager['performance']['bytes_hashed']} "
                f"before_stat={eager['performance']['stat_seconds']:.6f}s "
                f"before_hash={eager['performance']['hash_seconds']:.6f}s "
                f"before_db_lookup={eager['performance']['cache_lookup_seconds']:.6f}s "
                f"before_db_persist={eager_persist_seconds:.6f}s "
                f"after_ui_ready={lazy_seconds:.6f}s after_hash_count={lazy['performance']['sha256_calculated']} "
                f"after_bytes_hashed={lazy['performance']['bytes_hashed']} "
                f"after_enumeration={lazy['performance']['enumeration_seconds']:.6f}s "
                f"after_stat={lazy['performance']['stat_seconds']:.6f}s "
                f"after_db_lookup={lazy['performance']['cache_lookup_seconds']:.6f}s "
                f"after_db_persist={lazy['performance']['db_persist_seconds']:.6f}s "
                f"hash_deferred={lazy['performance']['hash_deferred']}"
            )
            self.assertEqual(eager["performance"]["sha256_calculated"], 2326)
            self.assertEqual(lazy["performance"]["sha256_calculated"], 0)
            self.assertEqual(lazy["performance"]["hash_deferred"], 2326)

    def test_10000_missing_files_are_isolated_as_errors(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            paths = [str(Path(root, f"missing-{index}.txt")) for index in range(10_000)]
            plan = build_incremental_analysis_plan(paths, self._db(root))
            self.assertEqual(plan["counts"]["errors"], 10_000)

    def test_10000_small_files_use_metadata_only(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            paths = []
            for index in range(10_000):
                path = Path(root, f"small-{index}.txt")
                path.write_bytes(b"x")
                paths.append(str(path))
            worker = IncrementalInventoryWorker(file_paths=paths, db_path=self._db(root))
            plans = []
            worker.completed.connect(plans.append)
            worker.run()
            self.assertEqual(plans[0]["counts"]["new"], 10_000)
            self.assertEqual(plans[0]["performance"]["sha256_calculated"], 0)
            self.assertEqual(plans[0]["performance"]["hash_deferred"], 10_000)

    def test_permission_and_delete_races_do_not_abort_scan(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            first = Path(root, "permission.txt")
            second = Path(root, "deleted.txt")
            third = Path(root, "ok.txt")
            for path in (first, second, third):
                path.write_text("x", encoding="utf-8")
            real_stat = os.stat

            def guarded(path, *args, **kwargs):
                if str(path) == str(first):
                    raise PermissionError("denied")
                if str(path) == str(second):
                    raise FileNotFoundError("deleted")
                return real_stat(path, *args, **kwargs)

            with patch("src.utils.core.os.stat", side_effect=guarded):
                plan = build_incremental_analysis_plan(
                    [str(first), str(second), str(third)], self._db(root)
                )
            self.assertEqual(plan["counts"]["errors"], 2)
            self.assertEqual(plan["counts"]["new"], 1)

    def test_5gb_file_is_registered_without_eager_hash(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            large = Path(root, "large.bin")
            with open(large, "wb") as stream:
                # Sparse file: exercises 5GB-safe stat/hash/cancellation without
                # allocating 5GB of test data on disk.
                stream.truncate(5 * 1024 * 1024 * 1024)
            worker = IncrementalInventoryWorker(file_paths=[str(large)], db_path=self._db(root))
            plans = []
            loop = QEventLoop()
            worker.completed.connect(plans.append)
            worker.finished.connect(loop.quit)
            with patch.object(worker, "_compute_hash", side_effect=AssertionError("eager hash")):
                worker.start()
                loop.exec()
            self.assertEqual(plans[0]["performance"]["sha256_calculated"], 0)
            self.assertEqual(plans[0]["performance"]["bytes_hashed"], 0)

    def test_changed_file_is_deferred_and_analysis_populates_hash(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            db = self._db(root)
            path = Path(root, "changed.txt")
            path.write_text("before", encoding="utf-8")
            first = build_incremental_analysis_plan([str(path)], db, defer_hash=True)
            registry = FileRegistryManager(db_path=db)
            self.assertTrue(registry.register_unanalyzed_file(str(path), first["new"][0])["success"])
            path.write_text("after-change", encoding="utf-8")
            changed = build_incremental_analysis_plan([str(path)], db, defer_hash=True)
            self.assertEqual(changed["counts"]["changed"], 1)
            self.assertEqual(changed["performance"]["sha256_calculated"], 0)

            result = registry.save_file_result(str(path), {"metadata": {"tags": ["test"]}})
            self.assertTrue(result["success"])
            conn = registry._connect()
            try:
                stored = conn.execute(
                    "SELECT file_hash,file_size,file_mtime_ns FROM files WHERE file_path=?", (str(path),)
                ).fetchone()
            finally:
                conn.close()
            self.assertTrue(stored[0])
            self.assertEqual(stored[0], FileRegistryManager.compute_file_hash(str(path)))

    def test_existing_hash_and_matching_metadata_are_reused(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            db = self._db(root)
            path = Path(root, "hashed.txt")
            path.write_text("stable", encoding="utf-8")
            registry = FileRegistryManager(db_path=db)
            self.assertTrue(registry.save_file_result(str(path), {"metadata": {"tags": ["stable"]}})["success"])
            with patch.object(FileRegistryManager, "compute_file_hash", side_effect=AssertionError("rehash")):
                plan = build_incremental_analysis_plan([str(path)], db, defer_hash=True)
            self.assertEqual(plan["counts"]["already_analyzed"], 1)
            self.assertEqual(plan["performance"]["hash_reused"], 1)


if __name__ == "__main__":
    unittest.main()
