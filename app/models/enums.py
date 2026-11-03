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
