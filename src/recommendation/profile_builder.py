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
                is_direct = record in direct_records
                weight = 1.0 if is_direct else self.DESCENDANT_WEIGHT
                total_weight += weight
                if record.extension:
                    extensions[record.extension.casefold()] += weight
                if record.analyzed:
                    analyzed_weight += weight
                    if record.category:
                        categories[record.category.casefold()] += weight
                    for tag in record.tags:
                        if tag:
                            tags[tag.casefold()] += weight
                    for kw in record.summary_keywords:
                        text_words[kw] += weight
                for kw in record.filename_keywords:
                    filename_words[kw] += weight
                for kw in record.text_keywords:
                    text_words[kw] += weight

            normalized_folder = os.path.normcase(os.path.abspath(folder_path))
            root_normalized = os.path.normcase(self.repository.managed_root)
            try:
                depth = len(Path(os.path.relpath(normalized_folder, root_normalized)).parts)
            except ValueError:
                depth = 0

            folder_name = os.path.basename(folder_path) or folder_path
            semantic_parts = tuple(
                part.casefold()
                for part in Path(os.path.relpath(folder_path, self.repository.managed_root)).parts
                if len(part) >= 2
            )

            fid = self.folder_id(folder_path)
            profiles[fid] = FolderProfile(
                folder_id=fid,
                folder_path=folder_path,
                folder_name=folder_name,
                parent_path=str(Path(folder_path).parent),
                depth=depth,
                direct_file_count=len(direct_records),
                descendant_file_count=len(descendant_records),
                extension_distribution=self._top(extensions),
                category_distribution=self._top(categories),
                tag_distribution=self._top(tags),
                filename_keywords=self._top(filename_words),
                text_keywords=self._top(text_words),
                metadata_coverage=analyzed_weight / total_weight if total_weight else 0.0,
                is_managed_root=(os.path.normcase(folder_path) == root_normalized),
                semantic_path_keywords=semantic_parts,
            )
        return profiles
