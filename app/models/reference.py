"""Синтетический справочник и его активные версии."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import DrugCategory


class ReferenceVersion(Base):
    __tablename__ = "reference_versions"
    __table_args__ = (
        Index(
            "uq_reference_versions_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    drugs: Mapped[list[ReferenceDrug]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    diagnoses: Mapped[list[ReferenceDiagnosis]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    rules: Mapped[list[ReferenceRule]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class ReferenceDrug(Base):
    __tablename__ = "reference_drugs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("reference_versions.id", ondelete="CASCADE"), index=True
    )
    trade_name: Mapped[str] = mapped_column(Text)
    normalized_trade_name: Mapped[str] = mapped_column(Text)
    mnn: Mapped[str] = mapped_column(Text)
    normalized_mnn: Mapped[str] = mapped_column(Text)
    version: Mapped[ReferenceVersion] = relationship(back_populates="drugs")


class ReferenceDiagnosis(Base):
    __tablename__ = "reference_diagnoses"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("reference_versions.id", ondelete="CASCADE"), index=True
    )
    diagnosis_id: Mapped[str] = mapped_column(Text)
    normalized_diagnosis_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text)
    version: Mapped[ReferenceVersion] = relationship(back_populates="diagnoses")


class ReferenceRule(Base):
    __tablename__ = "reference_rules"
    __table_args__ = (
        UniqueConstraint("version_id", "mnn", "diagnosis_id", name="uq_reference_rule"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("reference_versions.id", ondelete="CASCADE"), index=True
    )
    mnn: Mapped[str] = mapped_column(Text)
    normalized_mnn: Mapped[str] = mapped_column(Text)
    diagnosis_id: Mapped[str] = mapped_column(Text)
    normalized_diagnosis_id: Mapped[str] = mapped_column(Text)
    category: Mapped[DrugCategory | None] = mapped_column(
        Enum(
            DrugCategory,
            name="drug_category",
            values_callable=lambda cls: [value.value for value in cls],
        ),
        nullable=True,
    )
    source: Mapped[str | None] = mapped_column(Text)
    version: Mapped[ReferenceVersion] = relationship(back_populates="rules")
