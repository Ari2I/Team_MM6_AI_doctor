"""Все модели импортируются здесь для регистрации метаданных Alembic."""

from app.db.base import Base
from app.models.application import (
    Application,
    ApplicationDiagnosis,
    ApplicationItem,
    ValidationIssue,
)
from app.models.enums import ApplicationStatus, DrugCategory, TrafficLight

__all__ = [
    "Application",
    "ApplicationDiagnosis",
    "ApplicationItem",
    "ApplicationStatus",
    "Base",
    "DrugCategory",
    "TrafficLight",
    "ValidationIssue",
]
