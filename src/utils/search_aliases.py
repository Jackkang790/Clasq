"""Configurable equivalent spellings for local search.

Aliases expand one query concept and therefore never increase keyword coverage.
Project aliases are deliberately separate from general Korean/English aliases.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping


DEFAULT_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "맥": ("mac",),
    "mac": ("맥",),
}

def _environment_aliases() -> dict[str, tuple[str, ...]]:
    raw = os.getenv("SEARCH_PROJECT_ALIASES_JSON", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key).casefold(): tuple(str(item).casefold() for item in items if str(item))
        for key, items in value.items()
        if isinstance(items, (list, tuple))
    }


def build_search_alias_map(
    project_aliases: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, tuple[str, ...]]:
    merged = dict(DEFAULT_SEARCH_ALIASES)
    if project_aliases:
        merged.update({str(key).casefold(): tuple(values)
                       for key, values in project_aliases.items()})
    merged.update(_environment_aliases())
    return merged


def equivalent_terms(term: str, alias_map: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    normalized = term.casefold()
    return tuple(dict.fromkeys((normalized, *alias_map.get(normalized, ()))))
