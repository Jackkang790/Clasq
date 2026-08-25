from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .models import FileRecommendationContext, FolderProfile, FolderRecommendationResult
from .qwen_reranker import QwenFolderReranker
from .retriever import FolderCandidateRetriever


@dataclass(frozen=True)
class RecommendationConfig:
    none_threshold: float = 0.30
    ambiguous_margin: float = 0.05
    insufficient_metadata_threshold: float = 0.25
    fast_path_score: float = 0.75
    fast_path_margin: float = 0.15
    fast_path_metadata_coverage: float = 0.50
    top_k: int = 5


class FolderRecommendationService:
    def __init__(self, retriever: Optional[FolderCandidateRetriever] = None,
                 reranker: Optional[QwenFolderReranker] = None,
                 config: Optional[RecommendationConfig] = None):
        self.retriever = retriever or FolderCandidateRetriever()
        self.reranker = reranker
        self.config = config or RecommendationConfig()

    @staticmethod
    def _reason(candidate) -> str:
        contributions = sorted(
            ((name, value) for name, value in candidate.score_breakdown if value > 0),
            key=lambda item: -item[1],
        )
        return ", ".join(f"{name}={value:.2f}" for name, value in contributions[:3]) \
            or "유효한 유사 신호가 부족합니다."

    def recommend(self, context: FileRecommendationContext,
                  profiles: Mapping[str, FolderProfile]) -> FolderRecommendationResult:
        candidates = self.retriever.retrieve(context, profiles, self.config.top_k)
        retrieval_sec = self.retriever.last_elapsed_sec
        if not candidates:
            return FolderRecommendationResult(
                "NONE", "", "NONE", 0.0, False,
                "사용 가능한 실제 폴더 후보가 없습니다.", (),
                context.source_fingerprint, local_retrieval_sec=retrieval_sec)
        top = candidates[0]
        second_score = candidates[1].local_score if len(candidates) > 1 else 0.0
        margin = top.local_score - second_score
        if top.local_score < self.config.none_threshold:
            return FolderRecommendationResult(
                "NONE", "", "NONE", top.local_score, False,
                "로컬 retrieval 점수가 최소 기준보다 낮습니다.", candidates,
                context.source_fingerprint, local_retrieval_sec=retrieval_sec)
        if (margin < self.config.ambiguous_margin
                and context.metadata_coverage < self.config.insufficient_metadata_threshold):
            return FolderRecommendationResult(
                "NONE", "", "AMBIGUOUS", top.local_score, False,
                "후보 점수 차이가 작고 파일 metadata가 부족합니다.", candidates,
                context.source_fingerprint, local_retrieval_sec=retrieval_sec)
        if (top.local_score >= self.config.fast_path_score
                and margin >= self.config.fast_path_margin
                and context.metadata_coverage >= self.config.fast_path_metadata_coverage):
            return FolderRecommendationResult(
                top.folder_id, top.folder_path, "RECOMMENDED", top.local_score, False,
                self._reason(top), candidates, context.source_fingerprint,
                local_retrieval_sec=retrieval_sec)
        if self.reranker is None:
            return FolderRecommendationResult(
                "NONE", "", "REVIEW_REQUIRED", top.local_score, False,
                "의미 판단이 필요하지만 Qwen reranker가 비활성화되어 있습니다.", candidates,
                context.source_fingerprint, local_retrieval_sec=retrieval_sec)

        decision = self.reranker.rerank(context, candidates, profiles)
        if decision.status == "SELECTED":
            selected = next(item for item in candidates
                            if item.folder_id == decision.selected_folder_id)
            return FolderRecommendationResult(
                selected.folder_id, selected.folder_path, "RECOMMENDED",
                selected.local_score, True, decision.reason or self._reason(selected),
                candidates, context.source_fingerprint, decision.reason,
                decision.confidence, retrieval_sec, decision.elapsed_sec)
        status = "REVIEW_REQUIRED" if decision.status == "ERROR" else "NONE"
        return FolderRecommendationResult(
            "NONE", "", status, top.local_score, True,
            decision.reason or "Qwen이 유효한 후보를 선택하지 않았습니다.", candidates,
            context.source_fingerprint, decision.reason, decision.confidence,
            retrieval_sec, decision.elapsed_sec)
