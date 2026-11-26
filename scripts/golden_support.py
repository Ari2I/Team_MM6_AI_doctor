"""Снимки только фиксированного синтетического демо, без вывода данных в консоль."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models.enums import DrugCategory, ReviewReason, TrafficLight
from app.schemas.application_api import ApplicationCard

ROOT = Path(__file__).resolve().parents[1]
CASES = ("correct", "typo", "unknown", "two_diagnoses")
VOLATILE_FIELDS = {
    "id",
    "application_id",
    "request_id",
    "item_id",
    "diagnosis_id",
    "run_id",
    "created_at",
    "updated_at",
    "analyzed_at",
    "processing_seconds",
    "elapsed_seconds",
    "processing_time",
    "processing_time_ms",
    "duration_seconds",
}
PUBLIC_FIELDS = {
    "application",
    "filename",
    "status",
    "traffic_light",
    "requires_review",
    "summary",
    "reference_name",
    "reference_is_synthetic",
    "age_group",
    "warning_message",
    "category_shares",
    "disclaimer",
    "diagnoses",
    "items",
    "issues",
    "row_number",
    "drug_name",
    "mnn",
    "match_confidence",
    "amount",
    "checks",
    "diagnosis",
    "category",
    "category_label",
    "source",
    "reference_version",
    "comment",
    "needs_review",
    "review_reason",
    "level",
    "code",
    "message",
    "row",
    "column",
    "А",
    "Б",
    "В",
    "undetermined",
    "total_amount",
    "А_amount",
    "Б_amount",
    "В_amount",
    "undetermined_amount",
}
SAFE_VALUES = {value.value for value in DrugCategory} | {value.value for value in ReviewReason}
SAFE_VALUES |= {value.value for value in TrafficLight}


class GoldenError(Exception):
    """Техническая ошибка без URL, учётных данных и тела ответа."""


def normalize(value: Any) -> Any:
    """Удалить нестабильные поля; порядок всех списков сделать воспроизводимым."""
    if isinstance(value, dict):
        return {
            key: normalize(child)
            for key, child in sorted(value.items())
            if key not in VOLATILE_FIELDS
        }
    if isinstance(value, list):
        children = [normalize(child) for child in value]

        def order(child: Any) -> tuple[str, str]:
            identity = (
                {
                    key: child[key]
                    for key in ("row_number", "diagnosis", "row", "column", "code")
                    if key in child
                }
                if isinstance(child, dict)
                else child
            )
            return (
                json.dumps(identity, ensure_ascii=False, sort_keys=True),
                json.dumps(child, ensure_ascii=False, sort_keys=True),
            )

        return sorted(children, key=order)
    return value


def normalized_card(value: Any) -> dict[str, Any]:
    card = ApplicationCard.model_validate(value)
    if card.application.status != "analyzed" or card.application.reference_is_synthetic is not True:
        raise GoldenError("Нужен завершённый анализ с активным синтетическим справочником demo-v2.")
    if (
        not card.items
        or any(
            check.reference_version != "demo-api-2" for item in card.items for check in item.checks
        )
        or any(not item.checks for item in card.items)
    ):
        raise GoldenError("Снимок требует версии справочника demo-api-2 и проверок всех позиций.")
    return normalize(card.model_dump(mode="json"))


def fetch_cards(client: httpx.Client) -> dict[str, dict[str, Any]]:
    cards = {}
    for case in CASES:
        path = ROOT / "data" / "demo" / f"{case}.xlsx"
        with path.open("rb") as stream:
            response = client.post(
                "applications",
                files={
                    "file": (
                        path.name,
                        stream,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
        if response.status_code != 201:
            raise GoldenError("Загрузка демо не прошла; проверьте доступ и настройки стека.")
        value = response.json()
        card = normalized_card(value)
        identifier = str(ApplicationCard.model_validate(value).application.id)
        saved = client.get(f"applications/{identifier}")
        if saved.status_code != 200 or normalized_card(saved.json()) != card:
            raise GoldenError("Сохранённая карточка отличается от результата загрузки.")
        cards[case] = card
    return cards


def capture_from_stack() -> dict[str, dict[str, Any]]:
    """Настройки читает только явно запущенный CLI; .env не загружается."""
    url = os.environ.get("WEB_URL", "")
    user = os.environ.get("WEB_USER", "")
    password = os.environ.get("WEB_PASSWORD", "")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not user
        or not password
    ):
        raise GoldenError("Задайте WEB_URL, WEB_USER и WEB_PASSWORD без вывода их значений.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise GoldenError("HTTP с Basic Auth разрешён только для локального тестового стека.")
    # Не пересылать Authorization на адрес из redirect и не использовать proxy-настройки хоста.
    with httpx.Client(
        base_url=url.rstrip("/") + "/",
        auth=httpx.BasicAuth(user, password),
        timeout=120,
        verify=True,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        return fetch_cards(client)


def read_cards(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        case: json.loads((directory / f"{case}.json").read_text(encoding="utf-8")) for case in CASES
    }


def safe_value(value: Any) -> str:
    return value if isinstance(value, str) and value in SAFE_VALUES else "[значение скрыто]"


def differences(expected: Any, actual: Any, path: str = "$") -> list[str]:
    """Не выводить произвольные значения или неизвестные имена полей из снимка."""
    if type(expected) is not type(actual):
        return [f"{path}: изменён тип"]
    if isinstance(expected, dict):
        result = []
        for key in sorted(expected.keys() | actual.keys()):
            name = key if key in PUBLIC_FIELDS else "[поле]"
            child_path = f"{path}.{name}"
            if key not in expected:
                result.append(f"{child_path}: поле добавлено")
            elif key not in actual:
                result.append(f"{child_path}: поле отсутствует")
            else:
                result.extend(differences(expected[key], actual[key], child_path))
        return result
    if isinstance(expected, list):
        result = []
        if len(expected) != len(actual):
            result.append(f"{path}: изменено число элементов")
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            result.extend(differences(left, right, f"{path}[{index}]"))
        return result
    if expected != actual:
        if path.rsplit(".", 1)[-1] in {"category", "review_reason", "traffic_light"}:
            return [f"{path}: {safe_value(expected)} → {safe_value(actual)}"]
        return [f"{path}: значение изменено"]
    return []
