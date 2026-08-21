"""Environment-backed limits for the lightweight local text index."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class SearchIndexConfig:
    default_max_chars: int = field(
        default_factory=lambda: _positive_int("SEARCH_INDEX_MAX_CHARS", 500_000)
    )
    plain_text_max_chars: int = field(
        default_factory=lambda: _positive_int("SEARCH_INDEX_MAX_CHARS_TEXT", 1_000_000)
    )
    pdf_max_chars: int = field(
        default_factory=lambda: _positive_int("SEARCH_INDEX_MAX_CHARS_PDF", 500_000)
    )
    docx_max_chars: int = field(
        default_factory=lambda: _positive_int("SEARCH_INDEX_MAX_CHARS_DOCX", 500_000)
    )
    pptx_max_chars: int = field(
        default_factory=lambda: _positive_int("SEARCH_INDEX_MAX_CHARS_PPTX", 500_000)
    )

    def max_chars_for(self, extension: str) -> int:
        extension = extension.casefold()
        if extension == ".pdf":
            return self.pdf_max_chars
        if extension == ".docx":
            return self.docx_max_chars
        if extension == ".pptx":
            return self.pptx_max_chars
        if extension in {".txt", ".md", ".markdown", ".csv", ".json",
                         ".xml", ".yaml", ".yml"}:
            return self.plain_text_max_chars
        return self.default_max_chars
