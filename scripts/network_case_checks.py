"""Проверка полного МНН в публичном тексте только для синтетической приёмки."""

from __future__ import annotations

import re
import unicodedata


def normalize_public_text(value: str) -> str:
    """Унифицировать написание, сохранив пунктуацию и порядок слов."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").split())


def full_inn_pattern(inn: str | None) -> re.Pattern[str] | None:
    """Буквальное полное МНН без подстрок, синонимов и подбора словоформ.

    Регулярное выражение применяется к normalize_public_text(text). Отдельное
    слово многословного МНН не считается совпадением. Составные слова с дефисом
    не должны превращаться в совпадение по началу или окончанию названия.
    """
    if not inn or not (normalized := normalize_public_text(inn)):
        return None
    return re.compile(r"(?<![\w-])" + re.escape(normalized) + r"(?![\w-])")
