"""Общая декларативная база и метаданные моделей."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """База SQLAlchemy-моделей для приложения и Alembic."""
