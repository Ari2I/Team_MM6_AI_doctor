"""Все модели импортируются здесь для регистрации метаданных Alembic."""

from app.db.base import Base
from app.models.application import (
    Application,
    ApplicationDiagnosis,
    ApplicationItem,
    ItemCheck,
    ValidationIssue,
)
from app.models.enums import ApplicationStatus, DrugCategory, ReviewReason, TrafficLight
from app.models.reference import ReferenceDiagnosis, ReferenceDrug, ReferenceRule, ReferenceVersion

__all__ = [
    "Application",
    "ApplicationDiagnosis",
    "ApplicationItem",
    "ItemCheck",
    "ApplicationStatus",
    "Base",
    "DrugCategory",
    "ReviewReason",
    "TrafficLight",
    "ValidationIssue",
    "ReferenceVersion",
    "ReferenceDrug",
    "ReferenceDiagnosis",
    "ReferenceRule",
]
