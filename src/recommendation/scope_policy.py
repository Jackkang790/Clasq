from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from src.utils.core import DEFAULT_EXCLUDED_DIRECTORIES


class OrganizationScopePolicy(Protocol):
    def is_organizable_file(self, file_path: str, managed_root: str) -> bool: ...

    def is_destination_folder(self, folder_path: str, managed_root: str) -> bool: ...


class RootInboxOrganizationPolicy:
    """Organize root-level inbox files into safe, existing managed folders."""

    HARD_EXCLUDED = frozenset(DEFAULT_EXCLUDED_DIRECTORIES) | frozenset(
        {"_duplicates", "build", "dist", "cache", "tmp"}
    )
    INTERNAL_TREE_NAMES = frozenset(
        {"bin", "obj", "site-packages", "packages", "generated"}
    )
    PACKAGE_MARKERS = frozenset(
        {"package.json", "pyproject.toml", "requirements.txt", "packages.lock.json"}
    )

    @staticmethod
    def _normalized(path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(path)))

    @classmethod
    def _relative_parts(cls, path: str, managed_root: str):
        root = cls._normalized(managed_root)
        target = cls._normalized(path)
        try:
            if os.path.normcase(os.path.commonpath([root, target])) != root:
                return None
        except ValueError:
            return None
        relative = os.path.relpath(target, root)
        return () if relative == "." else Path(relative).parts

    @staticmethod
    def _has_package_marker(folder_path: str) -> bool:
        try:
            names = {
                entry.name.casefold()
                for entry in os.scandir(folder_path)
                if entry.is_file(follow_symlinks=False)
            }
        except OSError:
            return False
        return bool(names & RootInboxOrganizationPolicy.PACKAGE_MARKERS) or any(
            name.endswith(".nuspec") for name in names
        )

    def is_organizable_file(self, file_path: str, managed_root: str) -> bool:
        parts = self._relative_parts(file_path, managed_root)
        return bool(parts and len(parts) == 1 and os.path.isfile(file_path))

    def is_destination_folder(self, folder_path: str, managed_root: str) -> bool:
        parts = self._relative_parts(folder_path, managed_root)
        if parts is None or not os.path.isdir(folder_path) or os.path.islink(folder_path):
            return False
        try:
            if getattr(os.lstat(folder_path), "st_file_attributes", 0) & 0x400:
                return False
        except OSError:
            return False
        lowered = tuple(part.casefold() for part in parts)
        if any(part in self.HARD_EXCLUDED for part in lowered):
            return False
        if not parts:
            return True
        return not self.is_package_or_vendor_folder(folder_path, managed_root)

    def is_package_or_vendor_folder(self, folder_path: str, managed_root: str) -> bool:
        parts = self._relative_parts(folder_path, managed_root)
        if not parts:
            return False
        lowered = tuple(part.casefold() for part in parts)
        for index, part in enumerate(lowered):
            if part == "site-packages":
                return True
            if part in self.INTERNAL_TREE_NAMES and index >= 1:
                return True

        current = Path(managed_root)
        for index, part in enumerate(parts):
            current /= part
            if index < len(parts) - 1 and self._has_package_marker(str(current)):
                if lowered[index + 1] in self.INTERNAL_TREE_NAMES:
                    return True
        return False

    def is_hard_excluded_path(self, path: str, managed_root: str) -> bool:
        parts = self._relative_parts(path, managed_root)
        return parts is None or any(
            part.casefold() in self.HARD_EXCLUDED for part in parts
        )
