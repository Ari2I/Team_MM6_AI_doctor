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
    """Коды категорий в API, справочниках и PostgreSQL: А / Б / В."""

    A = "А"
    B = "Б"
    V = "В"


class ReviewReason(StrEnum):
    """Коды причин, по которым проверка требует внимания человека."""

    DRUG_NOT_FOUND = "DRUG_NOT_FOUND"
    DRUG_AMBIGUOUS = "DRUG_AMBIGUOUS"
    DIAGNOSIS_NOT_FOUND = "DIAGNOSIS_NOT_FOUND"
    NO_RULE_FOR_PAIR = "NO_RULE_FOR_PAIR"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    CONFLICTING_CATEGORIES = "CONFLICTING_CATEGORIES"
    AI_INPUT_INCOMPLETE = "AI_INPUT_INCOMPLETE"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    AI_TIMEOUT = "AI_TIMEOUT"
    AI_INVALID_RESPONSE = "AI_INVALID_RESPONSE"
    AI_PRELIMINARY = "AI_PRELIMINARY"
    AI_REVIEW_REQUIRED = "AI_REVIEW_REQUIRED"
