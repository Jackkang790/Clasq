"""Fail-closed filtering for PyInstaller root/runtime binary duplicates.

The application deliberately keeps llama.cpp beside its DLLs in ``runtime``.
PyInstaller's PE dependency traversal can additionally collect an imported DLL
at the one-dir root.  This module removes only reviewed root entries whose
source bytes exactly match the retained runtime entry.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


ROOT_RUNTIME_DUPLICATE_ALLOWLIST = frozenset({
    "cublasLt64_12.dll",
    "cublas64_12.dll",
    "cudart64_12.dll",
    "llama-server-impl.dll",
    "llama-common.dll",
    "llama.dll",
    "mtmd.dll",
    "ggml-base.dll",
    "libomp.dll",
    "ggml.dll",
})


def _normalized_destination(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exclude_verified_root_duplicates(
    binaries: Sequence[tuple[str, str, str]],
    *,
    expected_names: Iterable[str] = ROOT_RUNTIME_DUPLICATE_ALLOWLIST,
) -> tuple[list[tuple[str, str, str]], list[dict[str, object]]]:
    """Remove reviewed root duplicates while retaining ``runtime`` copies.

    Every expected component must have exactly one root entry and one runtime
    entry.  Both source files must exist and have identical size and SHA-256.
    Any mismatch aborts the build instead of risking an incomplete runtime.
    Entries outside the explicit allowlist are never changed.
    """

    entries = list(binaries)
    destinations: dict[str, list[tuple[int, tuple[str, str, str]]]] = {}
    for index, entry in enumerate(entries):
        destinations.setdefault(_normalized_destination(entry[0]).casefold(), []).append(
            (index, entry)
        )

    remove_indexes: set[int] = set()
    removed: list[dict[str, object]] = []
    for name in sorted(set(expected_names), key=str.casefold):
        root_matches = destinations.get(name.casefold(), [])
        runtime_destination = f"runtime/{name}"
        runtime_matches = destinations.get(runtime_destination.casefold(), [])
        if len(root_matches) != 1 or len(runtime_matches) != 1:
            raise RuntimeError(
                f"Expected exactly one {name!r} root entry and one "
                f"{runtime_destination!r} entry; found "
                f"{len(root_matches)} and {len(runtime_matches)}"
            )

        root_index, root_entry = root_matches[0]
        _, runtime_entry = runtime_matches[0]
        root_source = Path(root_entry[1]).resolve()
        runtime_source = Path(runtime_entry[1]).resolve()
        if not root_source.is_file() or not runtime_source.is_file():
            raise RuntimeError(
                f"Duplicate source missing for {name}: "
                f"{root_source} / {runtime_source}"
            )
        root_size = root_source.stat().st_size
        runtime_size = runtime_source.stat().st_size
        root_hash = _sha256(root_source)
        runtime_hash = _sha256(runtime_source)
        if root_size != runtime_size or root_hash != runtime_hash:
            raise RuntimeError(
                f"Refusing to exclude non-identical {name}: "
                f"root={root_size}:{root_hash}, runtime={runtime_size}:{runtime_hash}"
            )

        remove_indexes.add(root_index)
        removed.append({
            "relative_path": name,
            "runtime_path": runtime_destination,
            "source": str(root_source),
            "size": root_size,
            "sha256": root_hash,
        })

    return [entry for index, entry in enumerate(entries) if index not in remove_indexes], removed
