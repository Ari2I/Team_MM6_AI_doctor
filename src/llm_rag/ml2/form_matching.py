"""Ограниченное сопоставление концентрации с сохранением неопределённости."""

from dataclasses import dataclass
from decimal import Decimal
import re


@dataclass(frozen=True)
class FormMatch:
    status: str
    reason: str


_STRENGTH = re.compile(
    r"(?<![\d.,/])(\d+(?:[.,]\d+)?)\s*(мкг|мг|г|ме|%)"
    r"(?:\s*/\s*(?:(\d+(?:[.,]\d+)?)\s*)?(мл|л|доза|дозу))?(?![а-я])",
    re.I,
)


def _parse(text):
    if not text or "+" in text or re.search(r"\d\s+\d|\d\s*/\s*\d\s*(?:таб|кап)", text, re.I):
        return None
    matches = list(_STRENGTH.finditer(text))
    if len(matches) != 1:
        return None
    unmatched = text[: matches[0].start()] + text[matches[0].end() :]
    if "/" in unmatched or re.search(r"\d", unmatched):
        return None
    number, unit, denominator, per = matches[0].groups()
    amount = Decimal(number.replace(",", "."))
    unit = unit.lower()
    dimension = "mass" if unit in {"мкг", "мг", "г"} else unit
    amount *= {"мкг": Decimal(".001"), "мг": Decimal(1), "г": Decimal(1000)}.get(unit, Decimal(1))
    per = (per or "").lower().replace("дозу", "доза")
    if per:
        divisor = Decimal((denominator or "1").replace(",", "."))
        if divisor <= 0:
            return None
        if per == "л":
            divisor *= 1000
            per = "мл"
        amount /= divisor
    return dimension, per, amount


def match_form(dosage, registry_forms):
    """Сравнить только однозначно распознанные концентрации; схему приёма не выводить."""
    value = _parse(dosage)
    forms = [_parse(form) for form in registry_forms]
    if value is None or not forms:
        return FormMatch("UNKNOWN", "Концентрация не задана или не распознана; требуется сверка формы.")
    if value in forms:
        return FormMatch(
            "MATCH",
            "Концентрация совпала с записью реестра; тип лекарственной формы и схема приёма требуют отдельной сверки.",
        )
    if any(form is None or form[:2] != value[:2] for form in forms):
        return FormMatch("UNKNOWN", "Обозначения или единицы формы нельзя надёжно сопоставить.")
    return FormMatch(
        "MISMATCH",
        "Указанная концентрация не совпала с доступными записями реестра; МНН проверяется отдельно.",
    )
