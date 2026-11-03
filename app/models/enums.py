"""Значения перечислений, сохраняемые в PostgreSQL."""

from enum import StrEnum


class ApplicationStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    ANALYZED = "analyzed"
    ERROR = "error"


class TrafficLight(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    UNDETERMINED = "undetermined"


class DrugCategory(StrEnum):
    """Латинские A / B / V соответствуют русским категориям А / Б / В."""

    A = "A"
    B = "B"
    V = "V"


class ReviewReason(StrEnum):
    """Коды причин, по которым проверка требует внимания человека."""

    DRUG_NOT_FOUND = "DRUG_NOT_FOUND"
    DRUG_AMBIGUOUS = "DRUG_AMBIGUOUS"
    DIAGNOSIS_NOT_FOUND = "DIAGNOSIS_NOT_FOUND"
    NO_RULE_FOR_PAIR = "NO_RULE_FOR_PAIR"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    CONFLICTING_CATEGORIES = "CONFLICTING_CATEGORIES"
