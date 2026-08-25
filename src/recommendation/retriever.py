from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Iterable, Mapping, Tuple

from .family import FolderFamilyResolver
from .models import FileRecommendationContext, FolderCandidate, FolderProfile


@dataclass(frozen=True)
class RetrievalConfig:
    name: str = "name_text_heavy"
    folder_name_weight: float = 0.35
    filename_weight: float = 0.30
    tag_weight: float = 0.12
    category_weight: float = 0.03
    extension_weight: float = 0.02
    local_text_weight: float = 0.18
    root_penalty: float = 0.35
    top_k: int = 5


def _idf_map(documents: Iterable[Iterable[str]]) -> tuple:
    docs = [set(token.casefold() for token in document if token) for document in documents]
    document_count = len(docs)
    frequencies = {}
    for document in docs:
        for token in document:
            frequencies[token] = frequencies.get(token, 0) + 1
    return {
        token: math.log((document_count + 1) / (frequency + 1)) + 1.0
        for token, frequency in frequencies.items()
    }, document_count


def _idf_overlap(query: Iterable[str], document: Iterable[str],
                 idf: Mapping[str, float], document_count: int) -> float:
    query_tokens = {token.casefold() for token in query if token}
    document_tokens = {token.casefold() for token in document if token}
    if not query_tokens or not document_tokens:
        return 0.0
    unseen = math.log(document_count + 1) + 1.0
    denominator = sum(idf.get(token, unseen) for token in query_tokens)
    numerator = sum(idf.get(token, unseen) for token in query_tokens & document_tokens)
    return numerator / denominator if denominator else 0.0


class FolderCandidateRetriever:
    def __init__(self, config: RetrievalConfig | None = None):
        self.config = config or RetrievalConfig()
        self.last_elapsed_sec = 0.0
        self.family_resolver = FolderFamilyResolver()

    @staticmethod
    def _keys(distribution) -> Tuple[str, ...]:
        return tuple(item[0] for item in distribution)

    @staticmethod
    def _ratio(distribution, key: str) -> float:
        values = dict(distribution)
        total = sum(values.values())
        return min(1.0, float(values.get(key.casefold(), 0.0)) / total) if total else 0.0

    def retrieve(self, context: FileRecommendationContext,
                 profiles: Mapping[str, FolderProfile],
                 top_k: int | None = None) -> Tuple[FolderCandidate, ...]:
        started = time.perf_counter()
        config = self.config
        profile_values = tuple(profiles.values())
        filename_idf, count = _idf_map(
            self._keys(profile.filename_keywords) for profile in profile_values)
        tag_idf, _ = _idf_map(
            self._keys(profile.tag_distribution) for profile in profile_values)
        text_idf, _ = _idf_map(
            self._keys(profile.text_keywords) for profile in profile_values)
        path_idf, _ = _idf_map(
            profile.semantic_path_keywords for profile in profile_values)
        family_info = self.family_resolver.resolve(profiles)

        context_semantic = set(context.filename_keywords)
        raw_scored = []
        for profile in profile_values:
            features = {
                "folder_path": _idf_overlap(
                    context_semantic, profile.semantic_path_keywords, path_idf, count),
                "filename": _idf_overlap(
                    context.filename_keywords, self._keys(profile.filename_keywords),
                    filename_idf, count),
                "tag": _idf_overlap(
                    context.tags, self._keys(profile.tag_distribution), tag_idf, count),
                "text": _idf_overlap(
                    context.text_keywords, self._keys(profile.text_keywords), text_idf, count),
                "category": self._ratio(
                    profile.category_distribution, context.category) if context.category else 0.0,
                "extension": self._ratio(
                    profile.extension_distribution, context.extension) if context.extension else 0.0,
            }
            raw_score = (
                config.folder_name_weight * features["folder_path"]
                + config.filename_weight * features["filename"]
                + config.tag_weight * features["tag"]
                + config.local_text_weight * features["text"]
                + config.category_weight * features["category"]
                + config.extension_weight * features["extension"]
            )
            if profile.is_managed_root:
                raw_score *= (1.0 - config.root_penalty)
            info = family_info.get(profile.folder_id)
            penalty = info.structural_penalty if info else 0.0
            raw_score = max(0.0, raw_score - penalty)
            raw_scored.append((profile, raw_score, features, penalty))

        raw_scored.sort(key=lambda item: -item[1])

        k = top_k if top_k is not None else config.top_k
        # Diversity: limit each family to at most 2 candidates
        family_counts: dict = {}
        selected = []
        for profile, score, features, penalty in raw_scored:
            if len(selected) >= k:
                break
            info = family_info.get(profile.folder_id)
            fid = info.family_id if info else profile.folder_id
            if family_counts.get(fid, 0) >= 2:
                continue
            family_counts[fid] = family_counts.get(fid, 0) + 1
            selected.append((profile, score, features, penalty))

        self.last_elapsed_sec = time.perf_counter() - started

        result = []
        for rank, (profile, score, features, penalty) in enumerate(selected, start=1):
            info = family_info.get(profile.folder_id)
            result.append(FolderCandidate(
                folder_id=profile.folder_id,
                folder_path=profile.folder_path,
                folder_name=profile.folder_name,
                local_score=round(score, 4),
                rank=rank,
                score_breakdown=tuple(
                    (name, round(value, 4)) for name, value in features.items()
                ),
                depth=profile.depth,
                parent_folder_id=info.parent_folder_id if info else "",
                ancestor_folder_ids=info.ancestor_folder_ids if info else (),
                family_id=info.family_id if info else "",
                structural_penalty=penalty,
            ))
        return tuple(result)
