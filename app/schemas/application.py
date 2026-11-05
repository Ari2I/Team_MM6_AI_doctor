"""Pydantic-модели результатов анализа заявки и проверок пар."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ApplicationStatus, DrugCategory, ReviewReason, TrafficLight


class ItemCheckSchema(BaseModel):
    """Результат проверки пары «препарат + диагноз»."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    item_id: UUID
    diagnosis_id: UUID
    category: DrugCategory | None = None
    source: str | None = None
    reference_version: str | None = None
    comment: str | None = None
    needs_review: bool = False
    review_reason: ReviewReason | None = None


class ApplicationItemSchema(BaseModel):
    """Строка заявки с исходными данными и сопоставлением препарата."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    application_id: UUID
    row_number: int
    drug_name: str | None = None
    diagnosis: str | None = None
    dosage: str | None = None
    quantity: Decimal | None = None
    amount: Decimal | None = None
    mnn: str | None = None
    match_confidence: float | None = None
    match_note: str | None = None
    item_checks: list[ItemCheckSchema] = Field(default_factory=list)


class ApplicationSchema(BaseModel):
    """Заявка с агрегированным рекомендательным результатом."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    status: ApplicationStatus
    traffic_light: TrafficLight | None = None
    summary: str | None = None
    requires_review: bool = False
    warning_message: str | None = None
    category_shares: dict[str, float] | None = None
