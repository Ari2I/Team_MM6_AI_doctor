"""In-memory contracts used by the deterministic analysis services."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.enums import DrugCategory, ReviewReason
from app.services.text_utils import normalize_text


class ReferenceDrug(BaseModel):
    trade_name: str
    mnn: str
    normalized_trade_name: str | None = None
    normalized_mnn: str | None = None

    @model_validator(mode="after")
    def fill_normalized(self) -> ReferenceDrug:
        self.normalized_trade_name = normalize_text(self.trade_name)
        self.normalized_mnn = normalize_text(self.mnn)
        return self


class ReferenceDiagnosis(BaseModel):
    diagnosis_id: str
    name: str
    normalized_name: str | None = None

    @model_validator(mode="after")
    def fill_normalized(self) -> ReferenceDiagnosis:
        self.normalized_name = normalize_text(self.name)
        return self


class ReferenceRule(BaseModel):
    mnn: str
    diagnosis_id: str
    category: DrugCategory | None = None
    source: str | None = None
    normalized_mnn: str | None = None
    normalized_diagnosis_id: str | None = None

    @model_validator(mode="after")
    def fill_normalized(self) -> ReferenceRule:
        self.normalized_mnn = normalize_text(self.mnn)
        self.normalized_diagnosis_id = normalize_text(self.diagnosis_id)
        return self


class ReferenceSnapshot(BaseModel):
    diagnoses: list[ReferenceDiagnosis] = Field(default_factory=list)
    drugs: list[ReferenceDrug] = Field(default_factory=list)
    rules: list[ReferenceRule] = Field(default_factory=list)
    reference_version: str


class DrugMatch(BaseModel):
    mnn: str | None = None
    confidence: float = Field(ge=0, le=1)
    status: Literal["matched", "ambiguous", "not_found"]
    candidates: list[str] = Field(default_factory=list)


class DiagnosisMatch(BaseModel):
    diagnosis_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    status: Literal["matched", "ambiguous", "not_found"]


class PairCheck(BaseModel):
    category: DrugCategory | None = None
    source: str | None = None
    reference_version: str | None = None
    comment: str | None = None
    needs_review: bool = False
    review_reason: ReviewReason | None = None
