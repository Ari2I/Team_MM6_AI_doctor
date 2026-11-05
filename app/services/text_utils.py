"""Small, deterministic helpers for normalising reference and user text."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(value: object) -> str:
    """Return a case-insensitive, whitespace-normalised form of *value*.

    Russian ``ё`` is folded to ``е`` so that both spellings compare equally.
    """

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", text).strip()
