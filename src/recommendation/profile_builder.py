from __future__ import annotations

import hashlib
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping

from .folder_repository import FolderFileRecord, FolderProfileRepository, keywords
from .models import FolderProfile


class FolderProfileBuilder:
    DESCENDANT_WEIGHT = 0.25

    def __init__(self, repository: FolderProfileRepository):
        self.repository = repository
        self._folder_validity = {}

    def _is_valid_folder(self, folder_path: str) -> bool:
        normalized = self.repository.normalized(folder_path)
        if normalized not in self._folder_validity:
            self._folder_validity[normalized] = self.repository.is_valid_folder(folder_path)
        return self._folder_validity[normalized]

    @staticmethod
    def folder_id(folder_path: str) -> str:
        normalized = os.path.normcase(os.path.abspath(os.path.normpath(folder_path)))
        return "F_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12].upper()

    def _ancestors(self, file_path: str):
        root = self.repository.managed_root
        current = os.path.dirname(file_path)
        while True:
            try:
                if os.path.commonpath([root, current]) != root:
                    return
            except ValueError:
                return
            yield current
            if os.path.normcase(current) == os.path.normcase(root):
                return
            parent = os.path.dirname(current)
            if parent == current:
                return
            current = parent

    @staticmethod
    def _top(counter: Counter, limit: int = 30):
        return tuple((key, round(float(value), 3)) for key, value in counter.most_common(limit))

    def build(
        self,
        records: Iterable[FolderFileRecord],
        exclude_paths: Iterable[str] = (),
    ) -> Mapping[str, FolderProfile]:
        excluded = {self.repository.normalized(path) for path in exclude_paths}
        direct = defaultdict(list)
        descendants = defaultdict(list)
        for record in records:
            if self.repository.normalized(record.file_path) in excluded:
                continue
            parent = os.path.dirname(record.file_path)
            # Hard-excluded trees never contribute metadata. Package/vendor
            # trees may still inform safe ancestors, but are not destinations.
            if self.repository.is_hard_excluded_path(parent):
                continue
            direct[parent].append(record)
            for ancestor in self._ancestors(record.file_path):
                descendants[ancestor].append(record)

        profiles = {}
        for folder_path, descendant_records in descendants.items():
            if not descendant_records or not self._is_valid_folder(folder_path):
                continue
            direct_records = direct.get(folder_path, [])
            extensions, categories, tags = Counter(), Counter(), Counter()
            filename_words, text_words = Counter(), Counter()
            analyzed_weight = 0.0
            total_weight = 0.0
            for record in descendant_records:
                weight = 1.0 if os.path.dirname(record.file_path) == folder_path \
                    else self.DESCENDANT_WEIGHT
                total_weight += weight
                analyzed_weight += weight if record.analyzed else 0.0
                if record.extension:
                    extensions[record.extension] += weight
                if record.category:
                    categories[record.category.casefold()] += weight
                tags.update({tag.casefold(): weight for tag in record.tags})
                filename_words.update({word: weight for word in record.filename_keywords})
                text_words.update({
                    word: weight
                    for word in record.text_keywords + record.summary_keywords
                })
            relative = os.path.relpath(folder_path, self.repository.managed_root)
            depth = 0 if relative == "." else len(Path(relative).parts)
            semantic_path = () if relative == "." else keywords(
                " ".join(Path(relative).parts), limit=30
            )
            profile = FolderProfile(
                folder_id=self.folder_id(folder_path), folder_path=folder_path,
                folder_name=os.path.basename(folder_path) or folder_path,
                parent_path=os.path.dirname(folder_path), depth=depth,
                direct_file_count=len(direct_records),
                descendant_file_count=len(descendant_records),
                extension_distribution=self._top(extensions),
                category_distribution=self._top(categories),
                tag_distribution=self._top(tags),
                filename_keywords=self._top(filename_words),
                text_keywords=self._top(text_words),
                metadata_coverage=(analyzed_weight / total_weight if total_weight else 0.0),
                is_managed_root=(os.path.normcase(folder_path)
                                 == os.path.normcase(self.repository.managed_root)),
                semantic_path_keywords=semantic_path,
            )
            profiles[profile.folder_id] = profile
        return profiles
