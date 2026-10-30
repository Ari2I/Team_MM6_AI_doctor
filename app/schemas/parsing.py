"""Результат разбора Excel без проверки бизнес-правил."""

from decimal import Decimal

from pydantic import BaseModel, Field


class ParsedItem(BaseModel):
    """Строка заявки с исходным номером строки Excel (начиная с 1)."""

    row_number: int
    drug_name: str | None = None
    dosage: str | None = None
    quantity: Decimal | None = None
    amount: Decimal | None = None
    diagnosis: str | None = None


class ParseIssue(BaseModel):
    """Проблема разбора без содержимого ячеек; column — каноническое поле."""

    code: str
    message: str
    row: int | None = None
    column: str | None = None


class ParseResult(BaseModel):
    """Сопоставление хранит исходный заголовок как ключ, каноническое поле как значение."""

    items: list[ParsedItem] = Field(default_factory=list)
    issues: list[ParseIssue] = Field(default_factory=list)
    column_mapping: dict[str, str] = Field(default_factory=dict)
    missing_columns: list[str] = Field(default_factory=list)
    unknown_columns: list[str] = Field(default_factory=list)
