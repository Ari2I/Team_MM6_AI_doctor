"""Контракт HTTP-ответа и выгрузок."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import ApplicationStatus, TrafficLight
from app.schemas.validation import ValidationIssue
from llm_rag.contracts import Category, Citation, DISCLAIMER, ExplanationStatus


Mode = Literal["offline", "gigachat", "mixed"]
OFFICIAL_DISCLAIMER = "Отчёт содержит сведения из загруженных официальных источников для проверки специалистом. Решение о выплате принимает комиссия"


class DiagnosisResult(BaseModel):
    diagnosis_id: UUID
    name: str
    source_row: int | None = None


class ItemResult(BaseModel):
    item_id: UUID
    row_number: int
    drug_name: str | None
    diagnosis: str | None
    dosage: str | None
    quantity: Decimal | None
    amount: Decimal | None
    mnn: str | None
    original_cells: dict[str, str | None] = Field(default_factory=dict)
    match_candidates: list[str] = Field(default_factory=list)
    official_drug: OfficialDrug | None = None


class SourceCitation(Citation):
    url: str | None
    publisher: str | None
    origin: Literal["official", "synthetic"]
    retrieved_at: str | None


class OfficialRegistration(BaseModel):
    registration: str
    trade_name: str
    mnn: str | None
    form: str
    source: SourceCitation


class OfficialComposition(BaseModel):
    registration: str
    form: str
    source: SourceCitation


class OfficialDrug(BaseModel):
    registration_count: int
    registrations: list[OfficialRegistration]
    compositions: list[OfficialComposition]


class CheckResult(BaseModel):
    item_id: UUID
    diagnosis_id: UUID
    category: Category | None
    fragment_id: str | None
    reference_version: str | None
    needs_review: bool
    evidence_status: ExplanationStatus
    explanation: str
    reason_code: str
    evidence_reasons: list[str] = Field(default_factory=list)
    citations: list[SourceCitation] = Field(default_factory=list)
    review_sources: list[SourceCitation] = Field(default_factory=list)
    evidence_conditions: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    model_highlight: SourceCitation | None = None
    relationship_found: bool = False
    effective_mode: Mode
    retrieval: Literal["lexical", "vector"]
    fallback_reason: str | None = None


class DiagnosisSummary(BaseModel):
    diagnosis_id: UUID
    traffic_light: TrafficLight
    reason: str
    summary: str
    warning: str | None
    amounts: dict[str, Decimal]
    shares: dict[str, Decimal]
    confirmed_count: int
    pair_count: int


class ApplicationResult(BaseModel):
    application_id: UUID
    filename: str
    sheet_name: str | None = None
    status: ApplicationStatus
    diagnoses: list[DiagnosisResult]
    items: list[ItemResult]
    checks: list[CheckResult]
    issues: list[ValidationIssue]
    summary_by_diagnosis: list[DiagnosisSummary]
    known_total: Decimal
    requires_review: bool
    amounts_total: dict[str, Decimal] = Field(default_factory=dict)
    recommendation: str | None = None
    model_summary: list[SourceCitation] = Field(default_factory=list)
    model_attempted: bool = False
    approved_rule_count: int | None = None
    reference_version: str
    corpus_version: str
    effective_mode: Mode
    fallback_reason: str | None = None
    disclaimer: str = DISCLAIMER
    is_demo: bool = True
    reference_profile: Literal['demo', 'official'] = 'demo'
    official_coverage: Literal['registry_export', 'limited', 'historical'] | None = None
    official_as_of: str | None = None
