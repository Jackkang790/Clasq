"""Lightweight normalization helpers for local file search."""

from __future__ import annotations

import re


KOREAN_PARTICLES = (
    "에서", "으로", "을", "를", "은", "는", "이", "가", "과", "와", "의", "에", "로",
)

REQUEST_TOKEN_NORMALIZATION = {
    "파일을": "파일", "파일은": "파일", "파일에서": "파일",
    "문서를": "문서", "문서는": "문서", "문서에서": "문서",
    "폴더에": "폴더",
}


def _final_consonant_index(character: str) -> int | None:
    """Return the Hangul jongseong index, or None for a non-syllable."""
    codepoint = ord(character)
    if not 0xAC00 <= codepoint <= 0xD7A3:
        return None
    return (codepoint - 0xAC00) % 28


def _particle_agrees_with_stem(stem: str, particle: str) -> bool:
    """Apply only phonologically safe one-syllable particle removals."""
    jongseong = _final_consonant_index(stem[-1]) if stem else None
    if jongseong is None:
        return False
    has_batchim = jongseong != 0
    if particle in {"이", "을", "은", "과"}:
        return has_batchim
    if particle in {"가", "를", "는", "와"}:
        return not has_batchim
    if particle == "으로":
        return has_batchim and jongseong != 8  # ㄹ 받침은 '로'를 사용
    if particle == "로":
        return not has_batchim or jongseong == 8
    return len(stem) >= 2


def strip_korean_particle(token: str) -> str:
    """Remove one conservative Korean postposition from a non-trivial token."""
    if not token or not all("가" <= character <= "힣" for character in token):
        return token
    for particle in KOREAN_PARTICLES:
        if not token.endswith(particle):
            continue
        stem = token[:-len(particle)]
        if len(stem) >= 2 and _particle_agrees_with_stem(stem, particle):
            return stem
    return token


def normalize_query_token(token: str) -> str:
    cleaned = token.strip(".,!?;:'\"()[]{}").casefold()
    if cleaned in REQUEST_TOKEN_NORMALIZATION:
        return REQUEST_TOKEN_NORMALIZATION[cleaned]
    return strip_korean_particle(cleaned)


def normalize_search_text(value: str) -> str:
    """Build a comparison representation without changing the original value."""
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value or "")
    value = re.sub(r"[_\-.()\[\]{}]+", " ", value)
    return " ".join(value.casefold().split())


def search_variants(value: str) -> tuple[str, str]:
    normalized = normalize_search_text(value)
    return normalized, normalized.replace(" ", "")
