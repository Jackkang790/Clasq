"""Fail-closed exclusion for the unused Qt PDF image plugin chain."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Sequence


# PySide6/Qt 6.11.1 artifacts verified in the Batch 27 one-dir package.
# Clasq handles PDF documents with pypdf and has no Qt PDF rendering UI.
QTPDF_UNUSED_RUNTIME_ALLOWLIST = {
    "PySide6/plugins/imageformats/qpdf.dll": (
        42_296,
        "273a1b6daaf3c0def92902044cf35d0ec65e56f904acb7fbf428d80dae2bb9d5",
    ),
    "PySide6/Qt6Pdf.dll": (
        4_611_384,
        "cf8ae19cf5f98db4a3ae332fbe6bd00bc4cdf5ca5779f88b2071cf8180610106",
    ),
}


def _destination(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exclude_verified_unused_qtpdf_runtime(
    binaries: Sequence[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], list[dict[str, object]]]:
    """Remove only the reviewed qpdf plugin and its Qt6Pdf dependency."""
    entries = list(binaries)
    indexed: dict[str, list[tuple[int, tuple[str, str, str]]]] = {}
    for index, entry in enumerate(entries):
        indexed.setdefault(_destination(entry[0]).casefold(), []).append((index, entry))

    remove_indexes: set[int] = set()
    removed: list[dict[str, object]] = []
    for destination, (expected_size, expected_hash) in QTPDF_UNUSED_RUNTIME_ALLOWLIST.items():
        matches = indexed.get(destination.casefold(), [])
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one reviewed QtPdf artifact {destination!r}; found {len(matches)}"
            )
        index, entry = matches[0]
        source = Path(entry[1]).resolve()
        if not source.is_file():
            raise RuntimeError(f"Reviewed QtPdf artifact source missing: {source}")
        actual_size = source.stat().st_size
        actual_hash = _sha256(source)
        if actual_size != expected_size or actual_hash != expected_hash:
            raise RuntimeError(
                f"Refusing to exclude changed QtPdf artifact {destination}: "
                f"expected={expected_size}:{expected_hash}, actual={actual_size}:{actual_hash}"
            )
        remove_indexes.add(index)
        removed.append({
            "relative_path": destination,
            "source": str(source),
            "size": actual_size,
            "sha256": actual_hash,
        })

    return [entry for i, entry in enumerate(entries) if i not in remove_indexes], removed
