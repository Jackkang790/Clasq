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


def _idf_map(documents: Iterable[Iterable[str]]) -> tuple[dict[str, float], int]:
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

        # Destination path semantics are compared to the new file name only;
        # tags and content retain their own independently weighted channels.
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
            contributions = {
                "folder_path": features["folder_path"] * config.folder_name_weight,
                "filename": features["filename"] * config.filename_weight,
                "tag": features["tag"] * config.tag_weight,
                "text": features["text"] * config.local_text_weight,
                "category": features["category"] * config.category_weight,
                "extension": features["extension"] * config.extension_weight,
            }
            score = sum(contributions.values())
            if profile.is_managed_root:
                contributions["managed_root_penalty"] = -(score * (1.0 - config.root_penalty))
                score *= config.root_penalty
            raw_scored.append((score, profile, contributions))

        raw_scores = {profile.folder_id: score for score, profile, _ in raw_scored}
        scored = []
        for score, profile, contributions in raw_scored:
            info = family_info[profile.folder_id]
            parent_score = raw_scores.get(info.parent_folder_id)
            if parent_score is not None:
                parent_family = family_info[info.parent_folder_id].family_id
                if parent_family == info.family_id:
                    if profile.direct_file_count >= 2 and score > parent_score:
                        bonus = 0.02 * min(1.0, profile.direct_file_count / 5.0)
                        contributions["hierarchy_direct_support"] = bonus
                        score += bonus
                    elif profile.direct_file_count == 0:
                        contributions["hierarchy_empty_direct_penalty"] = -0.02
                        score = max(0.0, score - 0.02)
            if info.structural_penalty:
                penalty = score * info.structural_penalty
                contributions["structural_penalty"] = -penalty
                score -= penalty
            scored.append((score, profile, contributions, info))
        scored.sort(key=lambda item: (-item[0], item[1].depth,
                                     item[1].folder_path.casefold()))
        limit = top_k if top_k is not None else config.top_k
        selected = []
        family_counts = {}
        for item in scored:
            family_id = item[3].family_id
            if family_counts.get(family_id, 0) >= 2:
                continue
            selected.append(item)
            family_counts[family_id] = family_counts.get(family_id, 0) + 1
            if len(selected) >= limit:
                break
        candidates = tuple(
            FolderCandidate(
                folder_id=profile.folder_id,
                folder_path=profile.folder_path,
                folder_name=profile.folder_name,
                local_score=round(score, 6),
                rank=index,
                score_breakdown=tuple(
                    (key, round(value, 6)) for key, value in contributions.items()
                ),
                depth=profile.depth,
                parent_folder_id=info.parent_folder_id,
                ancestor_folder_ids=info.ancestor_folder_ids,
                family_id=info.family_id,
                structural_penalty=info.structural_penalty,
            )
            for index, (score, profile, contributions, info) in enumerate(
                selected, start=1
            )
        )
        self.last_elapsed_sec = time.perf_counter() - started
        return candidates
