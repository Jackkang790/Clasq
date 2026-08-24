"""Inspect PE direct and delay-load imports in a packaged distribution.

Requires the already-installed ``pefile`` used by the PyInstaller toolchain.
This development script is not part of the production package.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pefile


def inspect_pe(path: Path) -> dict | None:
    try:
        image = pefile.PE(str(path), fast_load=True)
        image.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
        ])
    except pefile.PEFormatError:
        return None
    direct = sorted({entry.dll.decode(errors="replace").lower()
                     for entry in getattr(image, "DIRECTORY_ENTRY_IMPORT", [])})
    delayed = sorted({entry.dll.decode(errors="replace").lower()
                      for entry in getattr(image, "DIRECTORY_ENTRY_DELAY_IMPORT", [])})
    image.close()
    return {"direct_imports": direct, "delay_imports": delayed}


def analyze(root: Path) -> dict:
    root = root.resolve()
    binaries = {}
    consumers = defaultdict(list)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".exe", ".dll", ".pyd"}:
            continue
        result = inspect_pe(path)
        if result is None:
            continue
        relative = path.relative_to(root).as_posix()
        binaries[relative] = result
        for imported in result["direct_imports"] + result["delay_imports"]:
            consumers[imported].append(relative)
    return {
        "root": str(root), "pe_file_count": len(binaries), "binaries": binaries,
        "consumers": {name: sorted(paths) for name, paths in sorted(consumers.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.dist)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"root": report["root"], "pe_file_count": report["pe_file_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
