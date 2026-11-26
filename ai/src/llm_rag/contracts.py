"""Версионированные контракты входа и выхода для обмена с предыдущим модулем."""

from decimal import Decimal
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, WithJsonSchema, model_validator


Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Category = Literal["А", "Б", "В"]
DosageStatus = Literal["VALID", "INVALID", "INSUFFICIENT_DATA", "NOT_CHECKED"]
ExplanationStatus = Literal[
    "SUPPORTED", "INSUFFICIENT_DATA", "UNCONFIRMED", "SOURCE_CONFLICT", "PROCESSING_ERROR"
]
MONEY_PATTERN = r"^[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?(?![\s\S])"


def _decimal(value):
    if not isinstance(value, (str, Decimal)):
        raise ValueError("Use a decimal string; JSON numbers and bool are not accepted")
    if isinstance(value, str) and re.fullmatch(MONEY_PATTERN, value) is None:
        raise ValueError("Use a nonnegative decimal string without whitespace or separators")
    try:
        return Decimal(value)
    except Exception as exc:
        raise ValueError("Invalid decimal") from exc


Money = Annotated[
    Decimal, BeforeValidator(_decimal), Field(ge=0, allow_inf_nan=False),
    WithJsonSchema({"type": "string", "pattern": MONEY_PATTERN}, mode="validation"),
]

# До 15 десятичных цифр на покупку, включая тысячные для трёхзнаковых валют.
AMOUNT_INTEGER_DIGITS = 12
AMOUNT_SCALE = 3
PURCHASE_AMOUNT_PATTERN = (
    rf"^[+]?(?:[0-9]{{1,{AMOUNT_INTEGER_DIGITS}}}(?:\.[0-9]{{0,{AMOUNT_SCALE}}})?"
    rf"|\.[0-9]{{1,{AMOUNT_SCALE}}})(?![\s\S])"
)


def _purchase_amount(value):
    if isinstance(value, Decimal):
        if (not value.is_finite() or value < 0 or value.as_tuple().exponent < -AMOUNT_SCALE
                or value.adjusted() >= AMOUNT_INTEGER_DIGITS):
            raise ValueError("Сумма покупки вне допустимого диапазона или точности.")
        # Приводим допустимый Decimal к тому же формату, что возвращается клиенту в JSON.
        return Decimal(format(value.copy_abs(), "f"))
    if not isinstance(value, str) or re.fullmatch(PURCHASE_AMOUNT_PATTERN, value) is None:
        raise ValueError("Сумма ожидается десятичной строкой до 12 целых и 3 дробных цифр.")
    return Decimal(value)


PurchaseMoney = Annotated[
    Decimal, BeforeValidator(_purchase_amount),
    WithJsonSchema({"type": "string", "pattern": PURCHASE_AMOUNT_PATTERN}),
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class Original(Model):
    trade_name: Text
    dosage: Text
    quantity: Annotated[int, Field(gt=0)]
    amount: PurchaseMoney
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class DosageCheck(Model):
    status: DosageStatus
    reason: Text | None
    missing_data: list[Text]


class AnalysisItem(Model):
    item_id: Text
    original: Original
    inn: Text | None
    category: Category | None
    classification_reason: Text | None
    dosage_check: DosageCheck
    source_refs: list[Text]
    upstream_status: Literal["PROCESSED", "PARTIAL", "FAILED"]
    missing_data: list[Text]


class AnalysisRequest(Model):
    schema_version: Literal["1.0"]
    request_id: Text
    diagnosis: Text
    items: Annotated[list[AnalysisItem], Field(min_length=1)]
    context: dict[Text, Text] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_items(self):
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("item_id must be unique within a request")
        return self


class Citation(Model):
    source_id: Text
    fragment_id: Text
    title: Text
    source_type: Literal["MASTER_LIST", "PROTOCOL", "DOSAGE"]
    version: Text
    location: Text
    quote: Text


class VerifiedClaim(Model):
    claim_id: Text
    kind: Literal["CATEGORY", "DOSAGE"]
    text: Text
    citations: Annotated[list[Citation], Field(min_length=1)]


class ItemResult(AnalysisItem):
    explanation_status: ExplanationStatus
    comment: str
    claims: list[VerifiedClaim]
    review_sources: list[Citation]
    reasons: list[Text]
    explanation_missing_data: list[Text]


class SummaryRef(Model):
    item_id: Text
    claim_id: Text


class CurrencyBreakdown(Model):
    currency: str
    total_amount: Money
    amounts_by_upstream_category: dict[str, Money]
    shares_by_upstream_category: dict[str, Decimal | None]


class RetrievalInfo(Model):
    method: Literal["lexical", "vector"]
    embedding_model: Text | None
    embedding_revision: Text | None
    embedding_dimension: Annotated[int, Field(gt=0)] | None
    corpus_fingerprint: Text


DISCLAIMER = (
    "Данный анализ носит рекомендательный характер и сформирован на основе сопоставления "
    "с клиническими протоколами. Финальное решение о жизненной необходимости сопутствующей "
    "терапии (Категория Б) принимает профильный врач"
)


class AnalysisResult(Model):
    schema_version: Literal["1.0"] = "1.0"
    request_id: Text
    diagnosis: Text
    items: list[ItemResult]
    summary_text: Text
    summary_refs: list[SummaryRef]
    requires_review: bool
    unresolved_item_ids: list[Text]
    completeness: Literal["COMPLETE", "PARTIAL", "UNRESOLVED"]
    costs_by_currency: list[CurrencyBreakdown]
    corpus_version: Text
    run_id: Text
    provider_name: Text
    retrieval_info: RetrievalInfo
    disclaimer: Literal[DISCLAIMER] = DISCLAIMER
