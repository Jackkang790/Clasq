from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Mapping, Tuple

from .folder_repository import keywords
from .models import FolderProfile


DECORATIVE_TOKENS = {
    "수정본", "최종", "복사본", "copy", "backup", "old", "archive",
    "temp", "tmp", "양식",
}
ARCHIVE_TOKENS = {"복사본", "copy", "backup", "old", "archive", "temp", "tmp"}
VERSION_PATTERN = re.compile(r"^v\d+$", re.IGNORECASE)
DATE_PATTERN = re.compile(r"^(?:19|20)\d{2}(?:\d{2}){0,2}$")


def _set(distribution) -> set[str]:
    return {str(item[0]).casefold() for item in distribution}


def _jaccard(left, right) -> float:
    left, right = set(left), set(right)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def meaningful_name_tokens(folder_name: str) -> Tuple[str, ...]:
    return tuple(
        token for token in keywords(folder_name, limit=30)
        if token not in DECORATIVE_TOKENS
        and not VERSION_PATTERN.match(token)
        and not DATE_PATTERN.match(token)
    )


def profile_similarity(left: FolderProfile, right: FolderProfile) -> float:
    return (
        0.40 * _jaccard(_set(left.filename_keywords), _set(right.filename_keywords))
        + 0.25 * _jaccard(_set(left.tag_distribution), _set(right.tag_distribution))
        + 0.20 * _jaccard(_set(left.category_distribution), _set(right.category_distribution))
        + 0.15 * _jaccard(left.semantic_path_keywords, right.semantic_path_keywords)
    )


@dataclass(frozen=True)
class FolderFamilyInfo:
    family_id: str
    parent_folder_id: str
    ancestor_folder_ids: Tuple[str, ...]
    structural_penalty: float = 0.0


class FolderFamilyResolver:
    """Conservative hierarchy/name/profile based folder-family resolver."""

    def resolve(self, profiles: Mapping[str, FolderProfile]):
        profile_values = tuple(profiles.values())
        by_path = {
            os.path.normcase(os.path.abspath(profile.folder_path)): profile
            for profile in profile_values
        }
        parent = {profile.folder_id: profile.folder_id for profile in profile_values}

        def find(folder_id):
            while parent[folder_id] != folder_id:
                parent[folder_id] = parent[parent[folder_id]]
                folder_id = parent[folder_id]
            return folder_id

        def union(left, right):
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[max(left_root, right_root)] = min(left_root, right_root)

        normalized_names = {}
        for profile in profile_values:
            normalized_names.setdefault(meaningful_name_tokens(profile.folder_name), []).append(profile)

        # Parent-child profiles need a strong profile match. Name-equivalent
        # folders also need profile evidence; no unrestricted fuzzy clustering.
        for profile in profile_values:
            parent_profile = by_path.get(os.path.normcase(os.path.abspath(profile.parent_path)))
            if parent_profile and profile_similarity(profile, parent_profile) >= 0.68:
                union(profile.folder_id, parent_profile.folder_id)
        for name_tokens, group in normalized_names.items():
            if not name_tokens or len(group) < 2:
                continue
            for index, left in enumerate(group):
                for right in group[index + 1:]:
                    if profile_similarity(left, right) >= 0.58:
                        union(left.folder_id, right.folder_id)

        penalties = {profile.folder_id: 0.0 for profile in profile_values}
        for profile in profile_values:
            raw_tokens = set(keywords(profile.folder_name, limit=30))
            if not (raw_tokens & ARCHIVE_TOKENS):
                continue
            siblings = [
                candidate for candidate in profile_values
                if candidate.folder_id != profile.folder_id
                and os.path.normcase(candidate.parent_path) == os.path.normcase(profile.parent_path)
            ]
            if any(profile_similarity(profile, sibling) >= 0.75 for sibling in siblings):
                penalties[profile.folder_id] = 0.12

        result = {}
        for profile in profile_values:
            ancestors = []
            current = os.path.normcase(os.path.abspath(profile.parent_path))
            while current in by_path:
                ancestor = by_path[current]
                ancestors.append(ancestor.folder_id)
                next_path = os.path.normcase(os.path.abspath(ancestor.parent_path))
                if next_path == current:
                    break
                current = next_path
            parent_profile = by_path.get(os.path.normcase(os.path.abspath(profile.parent_path)))
            root_id = find(profile.folder_id)
            family_hash = hashlib.sha256(root_id.encode("utf-8")).hexdigest()[:12].upper()
            result[profile.folder_id] = FolderFamilyInfo(
                family_id=f"FF_{family_hash}",
                parent_folder_id=parent_profile.folder_id if parent_profile else "",
                ancestor_folder_ids=tuple(ancestors),
                structural_penalty=penalties[profile.folder_id],
            )
        return result
