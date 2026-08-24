"""Fail-closed exclusion for the unused Qt Virtual Keyboard dependency chain."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Sequence


# PySide6/Qt 6.11.1 artifacts verified in the Batch 25 one-dir package.
# The plugin is the collection root; the remaining DLLs are its PE dependency
# chain.  Nothing outside these exact destinations and hashes is removed.
QT_UNUSED_RUNTIME_ALLOWLIST = {
    "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll": (33592, "d116b216a50037cbfa6ee9afd94bea357cdbee263e0f9b8c6ef2e811da29eb70"),
    "PySide6/Qt6Qml.dll": (5380920, "5dd60d8e2048557bc1b8a57f6bb44986f45d693c3b0930d1341b07089478f09d"),
    "PySide6/Qt6QmlMeta.dll": (160056, "1ae5a50e8defa2b60518a2261a2472ecf4d3e3ca965780339953d658844d4aa0"),
    "PySide6/Qt6QmlModels.dll": (997176, "1ec0d115747d65a994798064618ffd9e3e9efa1548297e64e08c49c9a98f0feb"),
    "PySide6/Qt6QmlWorkerScript.dll": (80696, "3a764b6c2bc8cb21f009485e7410e6d29b9edd5e0addbed8fb89292c0ab64c97"),
    "PySide6/Qt6Quick.dll": (6593336, "33bddb006a99c82ce3bb677fd2535dfb651d4b2fb12aa13eadde6dc442bf7f0d"),
    "PySide6/Qt6VirtualKeyboard.dll": (443192, "e8db083111dc264c80c81920c882ed1c2c28043edffa988894e0a82723b2aa4b"),
}


def _destination(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exclude_verified_unused_qt_runtime(
    binaries: Sequence[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], list[dict[str, object]]]:
    """Exclude exactly the reviewed plugin and its QML/Quick dependency chain."""
    entries = list(binaries)
    indexed: dict[str, list[tuple[int, tuple[str, str, str]]]] = {}
    for index, entry in enumerate(entries):
        indexed.setdefault(_destination(entry[0]).casefold(), []).append((index, entry))

    remove_indexes: set[int] = set()
    removed: list[dict[str, object]] = []
    for destination, (expected_size, expected_hash) in QT_UNUSED_RUNTIME_ALLOWLIST.items():
        matches = indexed.get(destination.casefold(), [])
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one reviewed Qt artifact {destination!r}; found {len(matches)}"
            )
        index, entry = matches[0]
        source = Path(entry[1]).resolve()
        if not source.is_file():
            raise RuntimeError(f"Reviewed Qt artifact source missing: {source}")
        actual_size = source.stat().st_size
        actual_hash = _sha256(source)
        if actual_size != expected_size or actual_hash != expected_hash:
            raise RuntimeError(
                f"Refusing to exclude changed Qt artifact {destination}: "
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
