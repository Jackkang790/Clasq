"""Generate a deterministic size/hash inventory for a PyInstaller one-dir tree.

This development tool is intentionally not imported by the application and is
not collected by ``clasq.spec``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


CUDA_NAMES = ("cuda", "cublas", "cudnn", "nvrtc", "nvjit", "nvblas", "nvml")
LLAMA_NAMES = ("llama", "ggml", "mtmd", "libomp")
PYTHON_PACKAGE_NAMES = {
    "certifi", "charset_normalizer", "docx", "lxml", "olefile", "openpyxl",
    "PIL", "pptx", "pydantic", "pydantic_core", "pypdf", "requests",
    "setuptools", "shiboken6", "tiktoken", "urllib3",
}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def categorize(relative_path: str) -> str:
    parts = Path(relative_path).parts
    lower_parts = tuple(part.lower() for part in parts)
    name = lower_parts[-1]
    # PyInstaller may copy dependencies both beside the Python executable and
    # into runtime/.  Classify by binary identity before directory placement.
    if any(marker in name for marker in CUDA_NAMES):
        return "CUDA / NVIDIA runtime"
    if any(marker in name for marker in LLAMA_NAMES):
        return "llama.cpp runtime"
    if "runtime" in lower_parts:
        if name == "ffmpeg.exe" or name.startswith("ffprobe"):
            return "FFmpeg"
        return "Bundled runtime other"
    if "pyside6" in lower_parts or name.startswith("qt6"):
        if "plugins" in lower_parts:
            return "Qt plugins"
        if "translations" in lower_parts:
            return "Qt translations"
        if "qml" in lower_parts:
            return "Qt QML"
        return "Qt / PySide6"
    if name in {"python313.dll", "python3.dll", "base_library.zip"}:
        return "Python runtime"
    if parts and parts[0] == "_internal" and parts[1:2]:
        package = parts[1]
        if package in PYTHON_PACKAGE_NAMES or name.endswith((".pyd", ".pyc")):
            return "Python packages / extensions"
    if "assets" in lower_parts:
        return "Application assets"
    if name == "clasq.exe":
        return "Application executable"
    return "Other"


def directory_bucket(relative_path: str, depth: int = 3) -> str:
    parent_parts = Path(relative_path).parent.parts
    if not parent_parts:
        return "."
    return "/".join(parent_parts[:depth])


def _totals(rows: list[dict], key: str) -> list[dict]:
    grouped: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        grouped[row[key]][0] += 1
        grouped[row[key]][1] += row["size"]
    return [
        {key: name, "file_count": values[0], "bytes": values[1]}
        for name, values in sorted(grouped.items(), key=lambda item: (-item[1][1], item[0]))
    ]


def build_inventory(root: Path) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    rows = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()),
                       key=lambda item: item.relative_to(root).as_posix().lower()):
        relative = path.relative_to(root).as_posix()
        rows.append({
            "relative_path": relative,
            "filename": path.name,
            "extension": path.suffix.lower(),
            "directory": path.parent.relative_to(root).as_posix() or ".",
            "directory_bucket": directory_bucket(relative),
            "category": categorize(relative),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })

    by_hash: dict[str, list[dict]] = defaultdict(list)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_hash[row["sha256"]].append(row)
        by_name[row["filename"].casefold()].append(row)

    duplicates = []
    for digest, group in by_hash.items():
        if len(group) > 1:
            size = group[0]["size"]
            duplicates.append({
                "sha256": digest, "size": size,
                "paths": [item["relative_path"] for item in group],
                "duplicate_bytes": size * (len(group) - 1),
            })
    duplicates.sort(key=lambda item: (-item["duplicate_bytes"], item["sha256"]))

    same_name_different_hash = []
    for filename, group in by_name.items():
        if len(group) > 1 and len({item["sha256"] for item in group}) > 1:
            same_name_different_hash.append({
                "filename": filename,
                "files": [{key: item[key] for key in ("relative_path", "size", "sha256")}
                          for item in group],
            })
    same_name_different_hash.sort(key=lambda item: item["filename"])

    total = sum(row["size"] for row in rows)
    return {
        "root": str(root),
        "file_count": len(rows),
        "total_bytes": total,
        "files": rows,
        "top_files": sorted(rows, key=lambda row: (-row["size"], row["relative_path"]))[:50],
        "category_totals": _totals(rows, "category"),
        "directory_totals": _totals(rows, "directory_bucket"),
        "duplicate_groups": duplicates,
        "duplicate_group_count": len(duplicates),
        "duplicate_bytes": sum(item["duplicate_bytes"] for item in duplicates),
        "same_name_different_hash": same_name_different_hash,
    }


def write_reports(inventory: dict, json_path: Path, csv_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=(
            "relative_path", "filename", "extension", "directory",
            "directory_bucket", "category", "size", "sha256",
        ))
        writer.writeheader()
        writer.writerows(inventory["files"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    inventory = build_inventory(args.dist)
    write_reports(inventory, args.json, args.csv)
    print(json.dumps({key: inventory[key] for key in (
        "root", "file_count", "total_bytes", "duplicate_group_count", "duplicate_bytes"
    )}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
