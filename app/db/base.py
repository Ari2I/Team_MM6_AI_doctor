"""Общая декларативная база и метаданные для будущих моделей."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Пустая база будущих SQLAlchemy-моделей для Alembic."""
