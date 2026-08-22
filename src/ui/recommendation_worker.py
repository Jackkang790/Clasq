from __future__ import annotations

import os
import time
from typing import Optional

from PySide6.QtCore import QThread, Signal

from src.recommendation.folder_repository import FolderProfileRepository
from src.recommendation.models import RecommendationPlanItem
from src.recommendation.profile_builder import FolderProfileBuilder
from src.recommendation.qwen_reranker import QwenFolderReranker
from src.recommendation.service import FolderRecommendationService


class FolderRecommendationWorker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(
        self,
        managed_root: str,
        scanned_paths: list[str],
        recommendation_paths: list[str],
        db_path: str = "file_manager.db",
        service: Optional[FolderRecommendationService] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.managed_root = managed_root
        self.scanned_paths = list(scanned_paths)
        self.recommendation_paths = list(dict.fromkeys(
            os.path.abspath(path) for path in recommendation_paths
        ))
        self.db_path = db_path
        self.service = service
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        started = time.perf_counter()
        try:
            repository = FolderProfileRepository(self.managed_root, self.db_path)
            records = repository.load_records(self.scanned_paths)
            profiles = FolderProfileBuilder(repository).build(records)
            contexts = {
                repository.normalized(record.file_path): repository.context_from_record(record)
                for record in records
            }
            service = self.service or FolderRecommendationService(
                reranker=QwenFolderReranker()
            )
            items = []
            qwen_times, retrieval_times = [], []
            total = len(self.recommendation_paths)
            for index, file_path in enumerate(self.recommendation_paths, start=1):
                if self._stop_requested:
                    payload = self._payload(profiles, items, started, retrieval_times, qwen_times)
                    self.cancelled.emit(payload)
                    return
                context = contexts.get(repository.normalized(file_path))
                if context is None:
                    continue
                result = service.recommend(context, profiles)
                retrieval_times.append(result.local_retrieval_sec)
                if result.qwen_used:
                    qwen_times.append(result.qwen_rerank_sec)
                items.append(RecommendationPlanItem(
                    file_path=file_path, current_folder=context.current_folder, result=result
                ))
                self.progress.emit(index, total, os.path.basename(file_path))
            self.completed.emit(
                self._payload(profiles, items, started, retrieval_times, qwen_times)
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    @staticmethod
    def _payload(profiles, items, started, retrieval_times, qwen_times):
        return {
            "profiles": profiles,
            "items": tuple(items),
            "stats": {
                "profile_count": len(profiles),
                "recommendation_count": len(items),
                "qwen_used_count": sum(item.result.qwen_used for item in items),
                "average_local_retrieval_sec": (
                    sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0.0
                ),
                "average_qwen_rerank_sec": (
                    sum(qwen_times) / len(qwen_times) if qwen_times else 0.0
                ),
                "elapsed_sec": time.perf_counter() - started,
            },
        }
