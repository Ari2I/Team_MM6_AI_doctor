"""Ответы API заявок для frontend и Swagger."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ApplicationStatus, DrugCategory, TrafficLight


class APIIssue(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    level: str = Field(description="Уровень замечания", examples=["warning"])
    code: str = Field(description="Код причины", examples=["empty_amount"])
    message: str = Field(description="Сообщение для пользователя", examples=["Не указана сумма."])
    row: int | None = Field(default=None, description="Номер строки Excel", examples=[2])
    column: str | None = Field(default=None, description="Поле с замечанием", examples=["amount"])


class APIErrors(BaseModel):
    errors: list[APIIssue] = Field(description="Ошибки запроса")


class ApplicationListEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(
        description="Идентификатор заявки", examples=["12345678-1234-1234-1234-123456789012"]
    )
    filename: str = Field(description="Имя исходного файла", examples=["sample_ok.xlsx"])
    status: ApplicationStatus = Field(description="Состояние обработки", examples=["analyzed"])
    traffic_light: TrafficLight | None = Field(
        default=None, description="Рекомендательный индикатор", examples=["green"]
    )
    requires_review: bool = Field(description="Нужна проверка человеком", examples=[False])
    created_at: datetime = Field(
        description="Время создания заявки", examples=["2026-11-05T10:00:00Z"]
    )


class ApplicationDetails(ApplicationListEntry):
    summary: str | None = Field(
        default=None,
        description="Резюме анализа",
        examples=["Результат носит рекомендательный характер."],
    )
    warning_message: str | None = Field(
        default=None,
        description="Предупреждение о проверке",
        examples=["Для 1 позиций требуется проверка человеком: NO_RULE_FOR_PAIR."],
    )
    category_shares: dict[str, float] | None = Field(
        default=None,
        description="Доли в процентах и суммы по категориям",
        examples=[{"A": 100.0, "B": 0.0, "V": 0.0, "undetermined": 0.0}],
    )


class CheckDetails(BaseModel):
    diagnosis: str = Field(description="Диагноз проверенной пары", examples=["Учебный диагноз 01"])
    category: DrugCategory | None = Field(default=None, description="Код категории", examples=["A"])
    category_label: str | None = Field(
        default=None, description="Название категории", examples=["Жизненно необходимые"]
    )
    source: str | None = Field(
        default=None, description="Источник правила", examples=["Синтетический демо-справочник"]
    )
    reference_version: str | None = Field(
        default=None, description="Версия справочника", examples=["demo-1"]
    )
    comment: str | None = Field(
        default=None, description="Шаблонный комментарий", examples=["Требуется проверка человеком"]
    )
    needs_review: bool = Field(description="Нужна проверка пары человеком", examples=[False])
    review_reason: str | None = Field(
        default=None, description="Код причины проверки", examples=["NO_RULE_FOR_PAIR"]
    )


class ItemDetails(BaseModel):
    row_number: int = Field(description="Номер строки Excel", examples=[2])
    drug_name: str | None = Field(
        default=None, description="Название из заявки", examples=["Учебный препарат"]
    )
    mnn: str | None = Field(
        default=None, description="Сопоставленное МНН", examples=["Учебное МНН"]
    )
    match_confidence: float | None = Field(
        default=None, description="Уверенность сопоставления от 0 до 1", examples=[1.0]
    )
    amount: Decimal | None = Field(default=None, description="Сумма строки", examples=["1200.00"])
    checks: list[CheckDetails] = Field(description="Проверки пар препарат — диагноз")


class ApplicationCard(BaseModel):
    application: ApplicationDetails = Field(description="Сводные данные заявки")
    disclaimer: str = Field(
        description="Обязательное предупреждение",
        examples=["Результат носит рекомендательный характер и требует проверки человеком."],
    )
    diagnoses: list[str] = Field(
        description="Уникальные диагнозы заявки", examples=[["Учебный диагноз 01"]]
    )
    items: list[ItemDetails] = Field(description="Строки и результаты проверок")
    issues: list[APIIssue] = Field(description="Замечания валидатора")
