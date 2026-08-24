"""Fail-closed exclusion for unused optional Pillow runtime extensions."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Sequence


# Pillow 12.3.0 / CPython 3.13 x64 artifacts verified in Batch 26.
# Clasq does not advertise AVIF and uses Qt Widgets rather than Tk.
PILLOW_UNUSED_RUNTIME_ALLOWLIST = {
    "PIL/_avif.cp313-win_amd64.pyd": (
        7_890_944,
        "2e3e5cce3aea38c603680ca3da5d161fad123ce23a803bb692a56aff608989be",
    ),
    "PIL/_imagingtk.cp313-win_amd64.pyd": (
        14_848,
        "8ccd6128481d5a3a15993c2f8a69585694ff972652a6b06dcaee276fc65633a7",
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


def exclude_verified_unused_pillow_runtime(
    binaries: Sequence[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], list[dict[str, object]]]:
    """Remove only the two reviewed optional Pillow extension binaries."""
    entries = list(binaries)
    indexed: dict[str, list[tuple[int, tuple[str, str, str]]]] = {}
    for index, entry in enumerate(entries):
        indexed.setdefault(_destination(entry[0]).casefold(), []).append((index, entry))

    remove_indexes: set[int] = set()
    removed: list[dict[str, object]] = []
    for destination, (expected_size, expected_hash) in PILLOW_UNUSED_RUNTIME_ALLOWLIST.items():
        matches = indexed.get(destination.casefold(), [])
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one reviewed Pillow artifact {destination!r}; found {len(matches)}"
            )
        index, entry = matches[0]
        source = Path(entry[1]).resolve()
        if not source.is_file():
            raise RuntimeError(f"Reviewed Pillow artifact source missing: {source}")
        actual_size = source.stat().st_size
        actual_hash = _sha256(source)
        if actual_size != expected_size or actual_hash != expected_hash:
            raise RuntimeError(
                f"Refusing to exclude changed Pillow artifact {destination}: "
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
