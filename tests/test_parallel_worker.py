import os
import shutil
import threading
import time
import unittest
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.utils.core import build_incremental_analysis_plan
from src.utils.main_processor import AnalysisResult, MainProcessor
from src.utils.workers import FolderScanAndTagWorker


RAW_SUCCESS = {
    "@TYPE": "@DB", "status": "SUCCESS", "file_info": {},
    "metadata": {"@TYPE": "@DB", "tags": ["test"], "ai_comment": "ok"},
    "error": None,
}
ROUTED_SUCCESS = {
    "response_type": "FILE_ORGANIZE",
    "payload": {"data": {"status": "SUCCESS", "error": None}},
}


class Tracker:
    def __init__(self, delay=0.03):
        self.delay = delay
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.paths = []
        self.processor_by_thread = {}
        self.created_processors = []


class AnalysisProcessor:
    def __init__(self, tracker, fail_names=None, gate=None):
        self.tracker = tracker
        self.fail_names = set(fail_names or [])
        self.gate = gate
        self.client_token = object()
        with tracker.lock:
            tracker.created_processors.append(self)

    def analyze_file(self, file_path, fingerprint):
        thread_id = threading.get_ident()
        with self.tracker.lock:
            self.tracker.active += 1
            self.tracker.max_active = max(self.tracker.max_active, self.tracker.active)
            self.tracker.paths.append(file_path)
            self.tracker.processor_by_thread.setdefault(thread_id, self)
        try:
            if self.gate:
                self.gate.wait(timeout=5)
            else:
                time.sleep(self.tracker.delay)
            if os.path.basename(file_path) in self.fail_names:
                raise RuntimeError("inference failed")
            return AnalysisResult(file_path, dict(RAW_SUCCESS))
        finally:
            with self.tracker.lock:
                self.tracker.active -= 1


class Coordinator:
    def __init__(self):
        self.save_threads = []
        self.saved_paths = []
        self.run_thread = None

    capture_fingerprint = staticmethod(MainProcessor.capture_fingerprint)

    def save_analyzed_result(self, file_path, analysis_result, fingerprint):
        self.save_threads.append(threading.get_ident())
        self.saved_paths.append(file_path)
        return ROUTED_SUCCESS


class RecordingRegistry:
    def __init__(self):
        self.calls = []

    def save_file_result(self, file_path, metadata_result):
        self.calls.append((file_path, metadata_result, threading.get_ident()))
        return {"success": True, "file_path": file_path, "is_duplicate": False}


class AudioExtractor:
    IMAGE_EXTENSIONS = ()
    AUDIO_VIDEO_EXTENSIONS = (".wav",)
    active = 0
    max_active = 0
    lock = threading.Lock()

    def is_image_file(self, _path):
        return False

    def is_media_file(self, _path):
        return True

    def process_media(self, _path):
        with self.lock:
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            time.sleep(0.05)
            return "audio text", "SUCCESS"
        finally:
            with self.lock:
                type(self).active -= 1


class TextExtractor:
    def is_image_file(self, _path):
        return False

    def is_media_file(self, _path):
        return False

    def extract(self, _path):
        return "document text", "SUCCESS"


class SimpleAnalyzer:
    def analyze_document_text(self, file_path, _text):
        return dict(RAW_SUCCESS)

    def _build_fallback_response(self, _info, error):
        return {"@TYPE": "@ERROR", "message": error}


class ParallelWorkerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("tests/fixtures/parallel_runtime").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _files(self, count, extension=".txt"):
        paths = []
        for index in range(count):
            path = self.root / f"file-{index:03d}{extension}"
            path.write_text(f"content-{index}", encoding="utf-8")
            paths.append(str(path))
        return paths

    def _run(self, paths, tracker, concurrency=2, coordinator=None, **kwargs):
        coordinator = coordinator or Coordinator()
        worker = FolderScanAndTagWorker(
            paths, main_processor=coordinator, batch_limit=None,
            total_pending=len(set(paths)), concurrency=concurrency,
            analysis_processor_factory=lambda: AnalysisProcessor(tracker, **kwargs),
        )
        completed = []
        worker.completed.connect(completed.append)
        coordinator.run_thread = threading.get_ident()
        worker.run()
        return worker, coordinator, completed[0]

    def test_concurrency_two_is_bounded_paths_once_and_sessions_thread_local(self):
        paths = self._files(50)
        tracker = Tracker()
        worker, coordinator, stats = self._run(paths + paths[:3], tracker)
        counts = Counter(tracker.paths)
        self.assertEqual(len(counts), 50)
        self.assertTrue(all(count == 1 for count in counts.values()))
        self.assertEqual(tracker.max_active, 2)
        self.assertEqual(worker._max_active_tasks, 2)
        self.assertEqual(stats["processed"], 50)
        self.assertEqual(stats["success"], 50)
        self.assertTrue(all(thread_id != coordinator.run_thread for thread_id in tracker.processor_by_thread))
        self.assertEqual(len({id(p.client_token) for p in tracker.created_processors}), 2)
        self.assertEqual(len(set(coordinator.save_threads)), 1)
        self.assertEqual(coordinator.save_threads[0], coordinator.run_thread)

    def test_concurrency_one_matches_sequential_execution(self):
        tracker = Tracker()
        worker, _coordinator, stats = self._run(self._files(5), tracker, concurrency=1)
        self.assertEqual(worker._max_active_tasks, 1)
        self.assertEqual(stats["success"], 5)

    def test_failure_isolated_and_later_tasks_continue(self):
        paths = self._files(5)
        tracker = Tracker()
        _worker, coordinator, stats = self._run(
            paths, tracker, fail_names={os.path.basename(paths[0])}
        )
        self.assertEqual(stats["processed"], 5)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["success"], 4)
        self.assertEqual(len(coordinator.saved_paths), 4)

    def test_cancellation_stops_submission_but_saves_running_tasks(self):
        paths = self._files(8)
        tracker = Tracker(delay=0)
        gate = threading.Event()
        coordinator = Coordinator()
        worker = FolderScanAndTagWorker(
            paths, main_processor=coordinator, batch_limit=None, total_pending=8,
            concurrency=2,
            analysis_processor_factory=lambda: AnalysisProcessor(tracker, gate=gate),
        )
        stopped = []
        worker.stopped.connect(stopped.append)
        thread = threading.Thread(target=worker.run)
        coordinator.run_thread = None
        thread.start()
        deadline = time.time() + 3
        while tracker.max_active < 2 and time.time() < deadline:
            time.sleep(0.01)
        coordinator.run_thread = thread.ident
        worker.request_stop()
        gate.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(tracker.paths), 2)
        self.assertEqual(len(coordinator.saved_paths), 2)
        self.assertEqual(worker.last_stats["processed"], 2)

    def test_stale_file_is_not_saved_and_next_plan_keeps_it_pending(self):
        path = self._files(1)[0]
        extractor = AudioExtractor()
        processor = MainProcessor(extractor, SimpleAnalyzer(), object(), db_path=":memory:")
        registry = RecordingRegistry()
        processor.registry = registry
        fingerprint = processor.capture_fingerprint(path)
        analysis = AnalysisResult(path, dict(RAW_SUCCESS))
        Path(path).write_text("changed-after-analysis", encoding="utf-8")
        routed = processor.save_analyzed_result(path, analysis, fingerprint)
        self.assertTrue(routed["payload"]["data"]["stale"])
        self.assertEqual(registry.calls, [])

        db_path = str(self.root / "plan.db")
        plan = build_incremental_analysis_plan([path], db_path)
        self.assertEqual(plan["counts"]["pending"], 1)

    def test_audio_whisper_section_has_max_concurrency_one(self):
        paths = self._files(2, extension=".wav")
        AudioExtractor.active = 0
        AudioExtractor.max_active = 0
        coordinator = Coordinator()

        def factory():
            return MainProcessor(
                AudioExtractor(), SimpleAnalyzer(), object(),
                db_path=":memory:", initialize_registry=False,
            )

        worker = FolderScanAndTagWorker(
            paths, main_processor=coordinator, batch_limit=None, total_pending=2,
            concurrency=2, analysis_processor_factory=factory,
        )
        worker.run()
        self.assertEqual(AudioExtractor.max_active, 1)
        self.assertEqual(worker._max_active_tasks, 2)

    def test_process_file_upload_remains_a_compatible_facade(self):
        path = self._files(1)[0]
        processor = MainProcessor(TextExtractor(), SimpleAnalyzer(), object(), db_path=":memory:")
        registry = RecordingRegistry()
        processor.registry = registry

        routed = processor.process_file_upload(path)

        self.assertEqual(routed["response_type"], "FILE_ORGANIZE")
        self.assertEqual(len(registry.calls), 1)
        self.assertEqual(registry.calls[0][0], path)


if __name__ == "__main__":
    unittest.main()
