"""Модели результатов проверки входного файла и распарсенной заявки."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator


class IssueLevel(StrEnum):
    """Серьёзность замечания валидатора."""

    CRITICAL = "critical"
    WARNING = "warning"


class IssueCode(StrEnum):
    """Коды проблем файла или заявки.

    Коды проверок дозировки и количества оставлены для совместимости;
    валидатор их больше не выдаёт. MULTIPLE_DIAGNOSES — информационное
    предупреждение, не влияющее на is_valid.
    """

    INVALID_EXTENSION = "invalid_extension"
    FILE_TOO_LARGE = "file_too_large"
    MISSING_COLUMN = "missing_column"
    NO_DATA = "no_data"
    NO_DIAGNOSIS = "no_diagnosis"
    EMPTY_DRUG_NAME = "empty_drug_name"
    EMPTY_AMOUNT = "empty_amount"
    EMPTY_QUANTITY = "empty_quantity"
    INVALID_NUMBER = "invalid_number"
    NON_POSITIVE_AMOUNT = "non_positive_amount"
    NON_POSITIVE_QUANTITY = "non_positive_quantity"
    EMPTY_DOSAGE = "empty_dosage"
    EMPTY_DIAGNOSIS = "empty_diagnosis"
    MULTIPLE_DIAGNOSES = "multiple_diagnoses"
    DUPLICATE_ROW = "duplicate_row"
    PARSE_ISSUE = "parse_issue"

    # Синонимы оставляют имена кода удобными для вызывающего кода, сохраняя
    # один стабильный набор значений в JSON.
    INVALID_FILE_EXTENSION = "invalid_extension"
    FILE_SIZE_EXCEEDED = "file_too_large"
    MISSING_REQUIRED_COLUMN = "missing_column"
    NO_DATA_ROWS = "no_data"
    DRUG_NAME_EMPTY = "empty_drug_name"
    AMOUNT_EMPTY = "empty_amount"
    QUANTITY_EMPTY = "empty_quantity"
    AMOUNT_NON_POSITIVE = "non_positive_amount"
    QUANTITY_NON_POSITIVE = "non_positive_quantity"
    DOSAGE_EMPTY = "empty_dosage"
    DIAGNOSIS_EMPTY = "empty_diagnosis"
    DUPLICATE = "duplicate_row"
    INVALID_AMOUNT = "invalid_number"
    INVALID_QUANTITY = "invalid_number"
    AMOUNT_INVALID = "invalid_number"
    QUANTITY_INVALID = "invalid_number"


class ValidationIssue(BaseModel):
    """Одно сообщение в формате code/message/row/column с уровнем серьёзности."""

    level: IssueLevel
    code: IssueCode
    message: str
    row: int | None = None
    column: str | None = None


class ValidationReport(BaseModel):
    """Итог проверки; счётчики и is_valid вычисляются из issues."""

    issues: list[ValidationIssue] = Field(default_factory=list)
    is_valid: bool = True
    critical_count: int = 0
    warning_count: int = 0

    @model_validator(mode="after")
    def calculate_summary(self) -> Self:
        self.critical_count = sum(issue.level is IssueLevel.CRITICAL for issue in self.issues)
        self.warning_count = sum(issue.level is IssueLevel.WARNING for issue in self.issues)
        self.is_valid = self.critical_count == 0
        return self
