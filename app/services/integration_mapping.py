"""Подготовка сохранённой заявки для фасада, без БД, сети и импортов AI при старте."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.models import Application
from app.models.enums import ReviewReason
from app.services.rules import pairs_for_item

# Только явно указанный латинский код в начале строки; диагноз не угадываем.
_ICD_PREFIX = re.compile(r"^\s*([A-Z][0-9]{2}(?:\.[0-9]{1,4})?)(?=\s|:|$)", re.I | re.ASCII)


class IntegrationMappingError(ValueError):
    """Безопасная причина отказа: никаких исходных значений или ошибок Pydantic."""

    def __init__(
        self,
        column: str | None = None,
        row: int | None = None,
        *,
        review_reason: ReviewReason = ReviewReason.AI_INPUT_INCOMPLETE,
    ) -> None:
        super().__init__("Не удалось подготовить заявку к ИИ-анализу. Требуется ручная проверка.")
        self.column = column
        self.row = row
        self.review_reason = review_reason


@dataclass(frozen=True, repr=False)
class PreparedIntegrationRequest:
    """Локальный результат подготовки, не новый API-контракт.

    payload соответствует общей части integration-request.1. age_group остаётся
    отдельно до согласования публичного поля библиотеки; валидация для worker
    запрещает её молчаливую потерю. Объект не предназначен для логирования.
    """

    payload: dict[str, Any]
    age_group: str | None
    rows_by_item_id: dict[str, int]


def _identifier(value: UUID | None, *, row: int | None = None) -> str:
    if not isinstance(value, UUID):
        raise IntegrationMappingError("id", row)
    return str(value)


def _text(value: str | None, column: str, row: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntegrationMappingError(column, row)
    return value


def _number(value: Decimal | None, column: str, row: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise IntegrationMappingError(column, row)
    # Никакого округления, умножения на количество и подстановки нуля.
    return format(value, "f")


def prepare_integration_request(
    application: Application, *, currency: str
) -> PreparedIntegrationRequest:
    """Чистое преобразование; diagnoses/items должны быть загружены вызывающим кодом.

    currency передаётся явно из AISettings.currency, настройки здесь не читаются.
    Числовые ограничения библиотечного контракта проверяет отдельная валидация:
    неподходящее известное значение не заменяется отсутствующим.
    """
    request_id = _identifier(application.id)
    if not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None:
        raise IntegrationMappingError("currency")
    age_group = application.age_group or None
    if age_group not in {None, "adult", "child"}:
        raise IntegrationMappingError("age_group")

    diagnoses: list[dict[str, str]] = []
    diagnosis_ids: set[str] = set()
    diagnosis_by_name: dict[str, str] = {}
    for diagnosis in application.diagnoses:
        identifier = _identifier(diagnosis.id)
        name = _text(diagnosis.name, "diagnosis")
        if identifier in diagnosis_ids or name in diagnosis_by_name:
            raise IntegrationMappingError("diagnosis")
        diagnosis_ids.add(identifier)
        diagnosis_by_name[name] = identifier
        value = {"diagnosis_id": identifier, "name": name}
        if match := _ICD_PREFIX.match(name):
            value["icd10"] = match[1].upper()
        diagnoses.append(value)
    if not diagnoses:
        raise IntegrationMappingError("diagnosis")
    diagnoses.sort(key=lambda diagnosis: diagnosis["diagnosis_id"])
    names = [diagnosis["name"] for diagnosis in diagnoses]

    purchases: list[dict[str, Any]] = []
    links: list[dict[str, str]] = []
    rows: dict[str, int] = {}
    items = list(application.items)
    if not items:
        raise IntegrationMappingError("drug_name")
    for item in items:
        identifier = _identifier(item.id, row=item.row_number)
        if identifier in rows:
            raise IntegrationMappingError("id", item.row_number)
        rows[identifier] = item.row_number
    for item in sorted(items, key=lambda item: (item.row_number, str(item.id))):
        identifier = str(item.id)
        purchases.append(
            {
                "item_id": identifier,
                "name": _text(item.drug_name, "drug_name", item.row_number),
                "inn": item.mnn,
                "dosage": item.dosage,
                "quantity": _number(item.quantity, "quantity", item.row_number),
                "amount": _number(item.amount, "amount", item.row_number),
                "currency": currency,
            }
        )
        for name in pairs_for_item(item.diagnosis, names):
            diagnosis_id = diagnosis_by_name.get(name)
            if diagnosis_id is None:
                raise IntegrationMappingError("diagnosis", item.row_number)
            links.append({"item_id": identifier, "diagnosis_id": diagnosis_id})
    return PreparedIntegrationRequest(
        payload={
            "schema_version": "integration-request.1",
            "request_id": request_id,
            "purchases": purchases,
            "diagnoses": diagnoses,
            "links": links,
        },
        age_group=age_group,
        rows_by_item_id=rows,
    )


def validate_integration_request(prepared: PreparedIntegrationRequest) -> dict[str, Any]:
    """Проверить публичной моделью перед будущей передачей worker; сеть не вызывается."""
    if prepared.age_group is not None:
        # TODO: согласовать публичное поле age_group с владельцем AI-библиотеки.
        raise IntegrationMappingError("age_group", review_reason=ReviewReason.AI_UNAVAILABLE)
    try:
        from llm_rag.integration_contracts import IntegrationRequest
    except ImportError:
        raise IntegrationMappingError(review_reason=ReviewReason.AI_UNAVAILABLE) from None
    try:
        return IntegrationRequest.model_validate(prepared.payload).model_dump(mode="json")
    except ValidationError as error:
        location = error.errors(include_input=False, include_context=False, include_url=False)[0][
            "loc"
        ]
        row = None
        column = None
        if len(location) >= 3 and location[0] == "purchases":
            purchase = prepared.payload["purchases"][location[1]]
            row = prepared.rows_by_item_id.get(purchase["item_id"])
            column = {"name": "drug_name", "inn": "mnn"}.get(location[2], str(location[2]))
        raise IntegrationMappingError(column, row) from None
