"""Последний AI-результат и снимок SQL-оснований, отдельно от item_checks."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApplicationAIResult(Base):
    __tablename__ = "application_ai_results"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(result) = 'object'", name="ck_ai_result_object"),
        CheckConstraint("jsonb_typeof(checks) = 'array'", name="ck_ai_checks_array"),
    )

    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True
    )
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    backend_revision: Mapped[str] = mapped_column(String(40))
    ai_revision: Mapped[str] = mapped_column(String(40))
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
