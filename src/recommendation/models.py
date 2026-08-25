from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Optional, Tuple


@dataclass(frozen=True)
class SourceFingerprint:
    file_size: int
    file_mtime_ns: int
    file_hash: str = ""

    @classmethod
    def capture(cls, file_path: str, file_hash: str = "") -> "SourceFingerprint":
        stat = os.stat(file_path)
        return cls(stat.st_size, stat.st_mtime_ns, file_hash or "")

    def matches(self, file_path: str) -> bool:
        try:
            current = os.stat(file_path)
        except OSError:
            return False
        return (current.st_size == self.file_size
                and current.st_mtime_ns == self.file_mtime_ns)


@dataclass(frozen=True)
class FileRecommendationContext:
    file_path: str
    file_name: str
    extension: str
    current_folder: str
    filename_keywords: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    category: str = ""
    text_keywords: Tuple[str, ...] = ()
    summary: str = ""
    metadata_coverage: float = 0.0
    source_fingerprint: Optional[SourceFingerprint] = None


@dataclass(frozen=True)
class FolderProfile:
    folder_id: str
    folder_path: str
    folder_name: str
    parent_path: str
    depth: int
    direct_file_count: int
    descendant_file_count: int
    extension_distribution: Tuple[Tuple[str, float], ...] = ()
    category_distribution: Tuple[Tuple[str, float], ...] = ()
    tag_distribution: Tuple[Tuple[str, float], ...] = ()
    filename_keywords: Tuple[Tuple[str, float], ...] = ()
    text_keywords: Tuple[Tuple[str, float], ...] = ()
    metadata_coverage: float = 0.0
    is_managed_root: bool = False
    semantic_path_keywords: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FolderCandidate:
    folder_id: str
    folder_path: str
    folder_name: str
    local_score: float
    rank: int
    score_breakdown: Tuple[Tuple[str, float], ...]
    depth: int = 0
    parent_folder_id: str = ""
    ancestor_folder_ids: Tuple[str, ...] = ()
    family_id: str = ""
    structural_penalty: float = 0.0


@dataclass(frozen=True)
class FolderRecommendationResult:
    selected_folder_id: str
    selected_folder_path: str
    status: str
    local_score: float
    qwen_used: bool
    reason: str
    candidates: Tuple[FolderCandidate, ...]
    source_fingerprint: Optional[SourceFingerprint]
    qwen_reason: str = ""
    qwen_confidence: Optional[float] = None
    local_retrieval_sec: float = 0.0
    qwen_rerank_sec: float = 0.0


@dataclass(frozen=True)
class RecommendationPlanItem:
    file_path: str
    current_folder: str
    result: FolderRecommendationResult
    review_status: str = "PENDING_REVIEW"
    chosen_folder_id: str = ""
    chosen_folder_path: str = ""

    def accept(self) -> "RecommendationPlanItem":
        if not self.result.selected_folder_id or self.result.selected_folder_id == "NONE":
            return self
        return replace(
            self, review_status="ACCEPTED",
            chosen_folder_id=self.result.selected_folder_id,
            chosen_folder_path=self.result.selected_folder_path,
        )

    def skip(self) -> "RecommendationPlanItem":
        return replace(self, review_status="SKIPPED", chosen_folder_id="",
                       chosen_folder_path="")

    def override(
        self,
        folder_id: str,
        folder_path: str = "",
        allowed_folder_ids: Tuple[str, ...] = (),
    ) -> "RecommendationPlanItem":
        candidate = next((item for item in self.result.candidates
                          if item.folder_id == folder_id), None)
        if candidate is None and folder_id not in allowed_folder_ids:
            raise ValueError("Override folder must be an existing managed folder.")
        selected_path = candidate.folder_path if candidate is not None else folder_path
        if not selected_path:
            raise ValueError("Override folder path is required.")
        return replace(self, review_status="OVERRIDDEN", chosen_folder_id=folder_id,
                       chosen_folder_path=selected_path)

    def refresh_stale(self) -> "RecommendationPlanItem":
        fingerprint = self.result.source_fingerprint
        if fingerprint is None or not fingerprint.matches(self.file_path):
            return replace(self, review_status="STALE", chosen_folder_id="",
                           chosen_folder_path="")
        return self
