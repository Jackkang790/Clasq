from __future__ import annotations

import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QThread, Signal

from .core import build_incremental_analysis_plan, scan_directory_files

if TYPE_CHECKING:
    from .main_processor import MainProcessor
    from .query_parser import SearchQueryParser


class FolderAnalysisPlanWorker(QThread):
    """Scan, hash and classify a folder without blocking the GUI thread."""

    progress = Signal(str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, folder_paths: list[str], db_path: str = "file_manager.db",
                 file_paths: Optional[list[str]] = None, excluded_directories=None):
        super().__init__()
        self.folder_paths = list(folder_paths)
        self.db_path = db_path
        self.file_paths = list(file_paths) if file_paths is not None else None
        self.excluded_directories = excluded_directories

    def run(self):
        started = time.perf_counter()
        try:
            self.progress.emit("지원 파일을 검색하고 있습니다...")
            if self.file_paths is None:
                files = []
                for folder_path in self.folder_paths:
                    files.extend(scan_directory_files(folder_path, self.excluded_directories))
                files = sorted(set(files), key=str.casefold)
            else:
                files = self.file_paths
            self.progress.emit(f"{len(files):,}개 파일의 변경 여부를 확인하고 있습니다...")
            plan = build_incremental_analysis_plan(files, self.db_path)
            self._register_same_content(plan)
            self.progress.emit("PPTX 본문 검색 인덱스를 갱신하고 있습니다...")
            from .local_text_index import LocalTextIndexer
            text_indexer = LocalTextIndexer(self.db_path)
            legacy_ppt = text_indexer.discover_legacy_ppt(self.folder_paths)
            plan["text_index"] = text_indexer.synchronize([*files, *legacy_ppt])
            print(f"[PERF] plan refresh: {time.perf_counter() - started:.3f} sec")
            perf = plan.get("performance", {})
            print(f"[PLAN PERF] scanned={len(plan['scanned'])}")
            print(f"[PLAN PERF] stat_only_skipped={perf.get('stat_only_skipped', 0)}")
            print(f"[PLAN PERF] sha256_calculated={perf.get('sha256_calculated', 0)}")
            print(f"[PLAN PERF] hash_backfilled={perf.get('hash_backfilled', 0)}")
            print(f"[PLAN PERF] changed_candidates={perf.get('changed_candidates', 0)}")
            print(f"[PLAN PERF] hash_errors={perf.get('hash_errors', 0)}")
            self.completed.emit(plan)
        except Exception as exc:
            self.error.emit(f"파일 분석 계획 생성 중 오류가 발생했습니다: {exc}")

    def _register_same_content(self, plan: dict) -> None:
        """Reuse metadata without invoking the duplicate quarantine policy."""
        from .db_manager import FileRegistryManager

        registry = FileRegistryManager(db_path=self.db_path)
        reused, failed = [], []
        for item in plan["same_content"]:
            result = registry.register_reused_analysis(
                item["file_path"], item["source_file_path"], item["file_hash"]
            )
            if result.get("success"):
                reused.append(item)
            else:
                pending_item = dict(item)
                pending_item["reason"] = "reuse_failed"
                pending_item["error"] = result.get("message", "metadata reuse failed")
                failed.append(pending_item)
        if failed:
            plan["same_content"] = reused
            plan["pending"].extend(failed)
            plan["errors"].extend(
                {"file_path": item["file_path"], "error": item["error"]}
                for item in failed
            )
        plan["counts"].update({
            "same_content": len(plan["same_content"]),
            "pending": len(plan["pending"]),
            "errors": len(plan["errors"]),
        })


class _LegacyFolderScanAndTagWorker(QThread):
    """Analyze only a precomputed pending batch with cooperative cancellation."""

    progress = Signal(str)
    progress_changed = Signal(int, int)
    statistics_changed = Signal(object)
    completed = Signal(object)
    stopped = Signal(object)
    error = Signal(str)

    def __init__(self, pending_files: list, main_processor: Optional[MainProcessor] = None,
                 db_path: str = "file_manager.db", batch_limit: Optional[int] = 50,
                 total_pending: Optional[int] = None):
        super().__init__()
        if batch_limit not in (50, 100, None):
            raise ValueError("batch_limit must be 50, 100 or None")
        normalized = [item["file_path"] if isinstance(item, dict) else str(item)
                      for item in pending_files]
        self.pending_files = normalized[:batch_limit] if batch_limit else normalized
        self.main_processor = main_processor
        self.db_path = db_path
        self.batch_limit = batch_limit
        self.total_pending = total_pending if total_pending is not None else len(normalized)
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def _get_processor(self):
        if self.main_processor is not None:
            return self.main_processor
        from .file_pipeline import FileAnalyzer, TextExtractor
        from .main_processor import MainProcessor
        from .query_parser import SearchQueryParser
        analyzer = FileAnalyzer()
        return MainProcessor(TextExtractor(), analyzer, SearchQueryParser(client=analyzer.client),
                             db_path=self.db_path)

    @staticmethod
    def _result_failed(result) -> bool:
        if not isinstance(result, dict):
            return True
        payload = result.get("payload", {})
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        return (result.get("response_type") == "ERROR"
                or data.get("status") == "FAILED" or bool(data.get("error")))

    def run(self):
        started = time.perf_counter()
        try:
            processor = self._get_processor()
        except Exception as exc:
            self.error.emit(f"AI 분석기를 초기화하지 못했습니다: {exc}")
            return
        total = len(self.pending_files)
        stats = {"batch_total": total, "processed": 0, "success": 0, "failed": 0,
                 "remaining": self.total_pending, "stopped": False, "errors": []}
        if total == 0:
            print(f"[PERF] batch AI finish: {time.perf_counter() - started:.3f} sec")
            self.completed.emit(stats)
            return
        for index, file_path in enumerate(self.pending_files, start=1):
            if self._stop_requested:
                stats["stopped"] = True
                print(f"[PERF] batch AI finish: {time.perf_counter() - started:.3f} sec")
                self.stopped.emit(stats)
                return
            self.progress.emit(f"AI 분석 {index} / {total}: {os.path.basename(file_path)}")
            try:
                result = processor.process_file_upload(file_path)
                if self._result_failed(result):
                    stats["failed"] += 1
                    stats["errors"].append({"file_path": file_path, "error": "분석 실패"})
                else:
                    stats["success"] += 1
            except Exception as exc:
                stats["failed"] += 1
                stats["errors"].append({"file_path": file_path, "error": str(exc)})
            stats["processed"] = index
            stats["remaining"] = max(0, self.total_pending - stats["success"])
            self.progress_changed.emit(index, total)
            self.statistics_changed.emit(dict(stats))
        stats["stopped"] = self._stop_requested
        print(f"[PERF] batch AI finish: {time.perf_counter() - started:.3f} sec")
        (self.stopped if self._stop_requested else self.completed).emit(stats)


class FolderScanAndTagWorker(QThread):
    """Bounded file-level AI concurrency with coordinator-only DB writes."""

    progress = Signal(str)
    progress_changed = Signal(int, int)
    statistics_changed = Signal(object)
    completed = Signal(object)
    stopped = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        pending_files: list,
        main_processor: Optional[MainProcessor] = None,
        db_path: str = "file_manager.db",
        batch_limit: Optional[int] = 50,
        total_pending: Optional[int] = None,
        concurrency: Optional[int] = None,
        analysis_processor_factory=None,
    ):
        super().__init__()
        if batch_limit not in (50, 100, None):
            raise ValueError("batch_limit must be 50, 100 or None")
        from src.ai.config import AIConfig

        requested_concurrency = AIConfig().ai_concurrency if concurrency is None else concurrency
        self.concurrency = max(1, min(4, int(requested_concurrency)))
        unique_items = []
        seen = set()
        for raw_item in pending_files:
            item = dict(raw_item) if isinstance(raw_item, dict) else {"file_path": str(raw_item)}
            item["file_path"] = os.path.abspath(os.path.normpath(item["file_path"]))
            key = os.path.normcase(item["file_path"])
            if key not in seen:
                seen.add(key)
                unique_items.append(item)
        self.pending_items = unique_items[:batch_limit] if batch_limit else unique_items
        self.pending_files = [item["file_path"] for item in self.pending_items]
        self.main_processor = main_processor
        self.db_path = db_path
        self.batch_limit = batch_limit
        self.total_pending = total_pending if total_pending is not None else len(unique_items)
        self.analysis_processor_factory = analysis_processor_factory
        self._stop_requested = False
        self._task_local = threading.local()
        self._active_lock = threading.Lock()
        self._active_tasks = 0
        self._max_active_tasks = 0
        self.last_stats = None

    def request_stop(self):
        self._stop_requested = True

    def _get_coordinator_processor(self):
        if self.main_processor is not None:
            return self.main_processor
        from .file_pipeline import FileAnalyzer, TextExtractor
        from .main_processor import MainProcessor
        from .query_parser import SearchQueryParser
        analyzer = FileAnalyzer()
        return MainProcessor(
            TextExtractor(), analyzer, SearchQueryParser(client=analyzer.client),
            db_path=self.db_path,
        )

    def _get_thread_analysis_processor(self):
        processor = getattr(self._task_local, "processor", None)
        if processor is None:
            if self.analysis_processor_factory is not None:
                processor = self.analysis_processor_factory()
            else:
                from .file_pipeline import FileAnalyzer, TextExtractor
                from .main_processor import MainProcessor
                from .query_parser import SearchQueryParser
                analyzer = FileAnalyzer()
                processor = MainProcessor(
                    TextExtractor(), analyzer, SearchQueryParser(client=analyzer.client),
                    db_path=self.db_path, initialize_registry=False,
                )
            self._task_local.processor = processor
        return processor

    @staticmethod
    def _result_data(result):
        if not isinstance(result, dict):
            return {}
        payload = result.get("payload", {})
        return payload.get("data", {}) if isinstance(payload, dict) else {}

    @classmethod
    def _result_failed(cls, result) -> bool:
        data = cls._result_data(result)
        return (not isinstance(result, dict) or result.get("response_type") == "ERROR"
                or data.get("status") == "FAILED" or bool(data.get("error")))

    @classmethod
    def _result_stale(cls, result) -> bool:
        data = cls._result_data(result)
        return bool(data.get("stale") or data.get("reason") == "changed_during_analysis")

    def _analyze_task(self, item, fingerprint):
        with self._active_lock:
            self._active_tasks += 1
            self._max_active_tasks = max(self._max_active_tasks, self._active_tasks)
        started = time.perf_counter()
        try:
            processor = self._get_thread_analysis_processor()
            result = processor.analyze_file(item["file_path"], fingerprint)
            return result, time.perf_counter() - started
        finally:
            with self._active_lock:
                self._active_tasks -= 1

    def _new_stats(self, total):
        return {
            "batch_total": total, "processed": 0, "success": 0, "failed": 0,
            "stale": 0, "remaining": self.total_pending, "stopped": False,
            "errors": [], "max_concurrent_tasks": 0,
        }

    def _emit_progress(self, stats):
        self.progress_changed.emit(stats["processed"], stats["batch_total"])
        self.statistics_changed.emit(dict(stats))

    def _print_perf(self, stats, started, file_times):
        elapsed = time.perf_counter() - started
        throughput = stats["processed"] / elapsed if elapsed else 0.0
        average = sum(file_times) / len(file_times) if file_times else 0.0
        stats["total_time_sec"] = round(elapsed, 3)
        stats["throughput_files_per_sec"] = round(throughput, 4)
        stats["avg_file_time_sec"] = round(average, 3)
        stats["max_concurrent_tasks"] = self._max_active_tasks
        print("[AI PERF]")
        print(f"batch_size={stats['batch_total']}")
        print(f"concurrency={self.concurrency}")
        print(f"processed={stats['processed']}")
        print(f"success={stats['success']}")
        print(f"failed={stats['failed']}")
        print(f"stale={stats['stale']}")
        print(f"total_time_sec={elapsed:.3f}")
        print(f"throughput_files_per_sec={throughput:.4f}")
        print(f"avg_file_time={average:.3f}")
        print(f"max_concurrent_tasks={self._max_active_tasks}")

    def _run_legacy_injected_processor(self, processor):
        """Compatibility path for old test/custom processors without split API."""
        started = time.perf_counter()
        stats = self._new_stats(len(self.pending_items))
        file_times = []
        for item in self.pending_items:
            if self._stop_requested:
                break
            item_started = time.perf_counter()
            try:
                result = processor.process_file_upload(item["file_path"])
                if self._result_failed(result):
                    stats["failed"] += 1
                else:
                    stats["success"] += 1
            except Exception as exc:
                stats["failed"] += 1
                stats["errors"].append({"file_path": item["file_path"], "error": str(exc)})
            file_times.append(time.perf_counter() - item_started)
            stats["processed"] += 1
            stats["remaining"] = max(0, self.total_pending - stats["success"])
            self._emit_progress(stats)
        stats["stopped"] = self._stop_requested
        self._print_perf(stats, started, file_times)
        self.last_stats = stats
        (self.stopped if stats["stopped"] else self.completed).emit(stats)

    def run(self):
        started = time.perf_counter()
        try:
            coordinator = self._get_coordinator_processor()
        except Exception as exc:
            self.error.emit(f"AI 분석기를 초기화하지 못했습니다: {exc}")
            return
        if not (hasattr(coordinator, "save_analyzed_result")
                and hasattr(coordinator, "capture_fingerprint")):
            self._run_legacy_injected_processor(coordinator)
            return

        total = len(self.pending_items)
        stats = self._new_stats(total)
        file_times = []
        if total == 0:
            self._print_perf(stats, started, file_times)
            self.last_stats = stats
            self.completed.emit(stats)
            return

        next_index = 0
        outstanding = {}

        def submit_one(executor):
            nonlocal next_index
            if self._stop_requested or next_index >= total:
                return False
            item = self.pending_items[next_index]
            next_index += 1
            try:
                fingerprint = coordinator.capture_fingerprint(
                    item["file_path"], item.get("file_hash")
                )
            except OSError as exc:
                stats["processed"] += 1
                stats["failed"] += 1
                stats["errors"].append({"file_path": item["file_path"], "error": str(exc)})
                stats["remaining"] = max(0, self.total_pending - stats["success"])
                self._emit_progress(stats)
                return True
            future = executor.submit(self._analyze_task, item, fingerprint)
            outstanding[future] = (item, fingerprint)
            return True

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            while len(outstanding) < self.concurrency and next_index < total:
                if not submit_one(executor):
                    break
            while outstanding:
                completed_futures, _ = wait(tuple(outstanding), return_when=FIRST_COMPLETED)
                for future in completed_futures:
                    item, fingerprint = outstanding.pop(future)
                    try:
                        analysis_result, file_time = future.result()
                        file_times.append(file_time)
                        routed = coordinator.save_analyzed_result(
                            item["file_path"], analysis_result, fingerprint
                        )
                        if self._result_stale(routed):
                            stats["stale"] += 1
                        elif self._result_failed(routed):
                            stats["failed"] += 1
                            stats["errors"].append({
                                "file_path": item["file_path"],
                                "error": str(self._result_data(routed).get("error", "analysis failed")),
                            })
                        else:
                            stats["success"] += 1
                    except Exception as exc:
                        stats["failed"] += 1
                        stats["errors"].append({"file_path": item["file_path"], "error": str(exc)})
                    stats["processed"] += 1
                    stats["remaining"] = max(0, self.total_pending - stats["success"])
                    stats["max_concurrent_tasks"] = self._max_active_tasks
                    self.progress.emit(
                        f"AI 분석 {stats['processed']} / {total}: {os.path.basename(item['file_path'])}"
                    )
                    self._emit_progress(stats)
                    if not self._stop_requested:
                        while len(outstanding) < self.concurrency and next_index < total:
                            if not submit_one(executor):
                                break

        stats["stopped"] = self._stop_requested
        stats["max_concurrent_tasks"] = self._max_active_tasks
        self._print_perf(stats, started, file_times)
        self.last_stats = stats
        (self.stopped if stats["stopped"] else self.completed).emit(stats)


class QueryParseWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, user_text: str, query_parser: SearchQueryParser):
        super().__init__()
        self.user_text = user_text
        self.query_parser = query_parser

    def run(self):
        try:
            self.finished.emit(self.query_parser.parse_user_query(self.user_text))
        except Exception as exc:
            self.error.emit(f"자연어 파싱 처리 중 오류가 발생했습니다: {exc}")
