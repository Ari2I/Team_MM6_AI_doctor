"""Хранение заявки, диагнозов, строк и замечаний валидации."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ApplicationStatus, DrugCategory, TrafficLight
from app.schemas.validation import IssueLevel


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    filename: Mapped[str] = mapped_column(Text)
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(
            ApplicationStatus,
            name="application_status",
            values_callable=lambda cls: [value.value for value in cls],
        ),
        default=ApplicationStatus.UPLOADED,
        server_default=ApplicationStatus.UPLOADED.value,
    )
    traffic_light: Mapped[TrafficLight | None] = mapped_column(
        Enum(
            TrafficLight,
            name="traffic_light",
            values_callable=lambda cls: [value.value for value in cls],
        )
    )
    summary: Mapped[str | None] = mapped_column(Text)
    requires_review: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    warning_message: Mapped[str | None] = mapped_column(Text)
    category_shares: Mapped[dict[str, float] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    diagnoses: Mapped[list[ApplicationDiagnosis]] = relationship(
        back_populates="application", cascade="all, delete-orphan", passive_deletes=True
    )
    items: Mapped[list[ApplicationItem]] = relationship(
        back_populates="application", cascade="all, delete-orphan", passive_deletes=True
    )
    validation_issues: Mapped[list[ValidationIssue]] = relationship(
        back_populates="application", cascade="all, delete-orphan", passive_deletes=True
    )


class ApplicationDiagnosis(Base):
    __tablename__ = "application_diagnoses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="diagnoses")
    item_checks: Mapped[list[ItemCheck]] = relationship(
        back_populates="diagnosis", cascade="all, delete-orphan", passive_deletes=True
    )


class ApplicationItem(Base):
    __tablename__ = "application_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    row_number: Mapped[int]
    # Пустые/неразобранные ячейки допустимы: валидатор возвращает предупреждения.
    drug_name: Mapped[str | None] = mapped_column(Text)
    diagnosis: Mapped[str | None] = mapped_column(Text)
    dosage: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric())
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    mnn: Mapped[str | None] = mapped_column(Text)
    match_confidence: Mapped[float | None]
    match_note: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="items")
    item_checks: Mapped[list[ItemCheck]] = relationship(
        back_populates="item", cascade="all, delete-orphan", passive_deletes=True
    )


class ItemCheck(Base):
    """Результат проверки отдельной пары «препарат + диагноз»."""

    __tablename__ = "item_checks"
    __table_args__ = (
        UniqueConstraint("item_id", "diagnosis_id", name="uq_item_checks_item_diagnosis"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    item_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_items.id", ondelete="CASCADE"), index=True
    )
    diagnosis_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_diagnoses.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[DrugCategory | None] = mapped_column(
        Enum(
            DrugCategory,
            name="drug_category",
            values_callable=lambda cls: [value.value for value in cls],
        ),
        comment="A / B / V — категории А / Б / В; NULL означает, что категория не определена.",
    )
    source: Mapped[str | None] = mapped_column(Text)
    reference_version: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    review_reason: Mapped[str | None] = mapped_column(Text)

    item: Mapped[ApplicationItem] = relationship(back_populates="item_checks")
    diagnosis: Mapped[ApplicationDiagnosis] = relationship(back_populates="item_checks")


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[IssueLevel] = mapped_column(
        Enum(
            IssueLevel,
            name="issue_level",
            values_callable=lambda cls: [value.value for value in cls],
        )
    )
    code: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    row: Mapped[int | None]
    column: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="validation_issues")
