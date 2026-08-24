"""Validate and summarize Clasq's third-party component inventory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


VALID_STATUSES = {"VERIFIED", "REVIEW REQUIRED", "UNKNOWN / BLOCKER"}


def load_inventory(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_inventory(data)


def validate_inventory(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != 1:
        raise ValueError("unsupported third-party inventory schema")
    components = data.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("third-party inventory must contain components")
    ids: set[str] = set()
    for component in components:
        component_id = component.get("id")
        if not component_id or component_id in ids:
            raise ValueError(f"missing or duplicate component id: {component_id!r}")
        ids.add(component_id)
        for required in ("name", "version", "role", "license", "evidence", "status"):
            if not component.get(required):
                raise ValueError(f"{component_id}: missing {required}")
        if component["status"] not in VALID_STATUSES:
            raise ValueError(f"{component_id}: invalid status {component['status']!r}")
        if not isinstance(component.get("bundled"), bool):
            raise ValueError(f"{component_id}: bundled must be boolean")
    return data


def validate_license_files(inventory: dict[str, Any], repository: Path) -> list[str]:
    missing: list[str] = []
    for component in inventory["components"]:
        if not component["bundled"]:
            continue
        for relative in component.get("license_files", []):
            if not (repository / relative).is_file():
                missing.append(f"{component['id']}:{relative}")
    return missing


def summarize(inventory: dict[str, Any]) -> dict[str, int]:
    summary = {status: 0 for status in VALID_STATUSES}
    summary["bundled"] = 0
    summary["not_bundled"] = 0
    for component in inventory["components"]:
        summary[component["status"]] += 1
        summary["bundled" if component["bundled"] else "not_bundled"] += 1
    return summary


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    inventory = load_inventory(repository / "packaging" / "third-party-components.json")
    missing = validate_license_files(inventory, repository)
    if missing:
        raise SystemExit("Missing license files: " + ", ".join(missing))
    print(json.dumps(summarize(inventory), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
