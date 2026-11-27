"""Обёртка сохранённого human-report.1 без зависимости от llm_rag."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import DrugCategory
from app.schemas.analysis import PairCheck

Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportSource(ReportModel):
    number: int = Field(ge=1)
    title: str
    version: str
    location: str
    quote: str
    url: str
    copy_url: str | None


class ReportStatement(ReportModel):
    text: str
    sources: list[ReportSource]


class ReportQuestion(ReportStatement):
    why: str


class ReportItem(ReportModel):
    number: int = Field(ge=1)
    name: str
    diagnosis: str
    category: str
    evidence: str
    comment: str
    established: list[ReportStatement]
    uncertain: list[ReportStatement]
    questions: list[ReportQuestion]
    actions: list[str]
    sources: list[ReportSource]


class ReportCost(ReportModel):
    amount: str
    currency: str
    currency_label: str
    unknown_amount_count: int = Field(ge=0)


class HumanReport(ReportModel):
    schema_version: Literal["human-report.1"]
    title: str
    notice: str
    summary: str
    recommendation_note: str
    review_note: str
    items: list[ReportItem]
    costs: list[ReportCost]
    purchases_without_amount_count: int = Field(ge=0)


class AIReportCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1)
    item_id: UUID
    diagnosis_id: UUID
    row_number: int = Field(ge=1)
    backend_rule: PairCheck
    ai_rule_category: DrugCategory | None = None
    ai_proposed_category: DrugCategory | None = None
    classification_basis: Literal["APPROVED_RULE", "ML2_PROPOSAL", "CONFLICT", "NONE"] | None = None
    conflict: bool
    processed: bool
    generation_mode: Literal["OFFLINE", "MODEL", "FALLBACK"] | None = None
    form_status: Literal["MATCH", "MISMATCH", "UNKNOWN"] | None = None
    reasons: list[str]


class AIReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ai-report.1"] = "ai-report.1"
    application_id: UUID
    saved_at: datetime
    backend_revision: Revision
    ai_revision: Revision
    completeness: Literal["COMPLETE", "PARTIAL"]
    human_report: HumanReport
    checks: list[AIReportCheck]

    @model_validator(mode="after")
    def valid_row_numbers(self) -> "AIReport":
        rows = self.human_report.items
        if self.saved_at.tzinfo is None:
            raise ValueError("Время сохранения должно включать часовой пояс.")
        numbers = list(range(1, len(rows) + 1))
        if [c.number for c in self.checks] != numbers or any(
            row.number != number for number, row in zip(numbers, rows, strict=True)
        ):
            raise ValueError("Нарушено соответствие строк сохранённого отчёта.")
        pairs = [(c.item_id, c.diagnosis_id) for c in self.checks]
        if len(pairs) != len(set(pairs)):
            raise ValueError("Пары сохранённого отчёта повторяются.")
        return self
