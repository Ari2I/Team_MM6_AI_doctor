"""Technical reasons remain machine-readable while product text stays readable."""

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from openpyxl import load_workbook

from app.config import MODEL_ERROR_LABELS, REVIEW_REASON_LABELS
from app.models.enums import ReviewReason
from app.services.presentation import humanize_warning_text
from app.services.report import build_report
from app.services.summary import build_warning_message


@pytest.fixture()
def card() -> dict[str, Any]:
    value = json.loads(Path("sample_api_response.json").read_text(encoding="utf-8"))
    checks = value["items"][0]["checks"]
    checks.append(dict(checks[0]))
    return value


@pytest.mark.parametrize("reason", list(ReviewReason))
def test_warning_covers_every_review_reason(reason: ReviewReason) -> None:
    message = build_warning_message([{"needs_review": True, "review_reason": reason}])
    assert REVIEW_REASON_LABELS[reason].rstrip(".") in message
    assert reason.value not in message
    assert "Для 1 позиции" in message


@pytest.mark.parametrize(
    "count,word",
    [(1, "позиции"), (2, "позиций"), (11, "позиций"), (21, "позиции"), (111, "позиций")],
)
def test_warning_counts_distinct_items(count: int, word: str) -> None:
    checks = [
        {"item_id": item, "needs_review": True, "review_reason": "AI_PRELIMINARY"}
        for item in range(count)
    ]
    assert build_warning_message(checks + checks).startswith(f"Для {count} {word}")


def test_unknown_reason_is_not_exposed_and_no_review_has_no_warning() -> None:
    assert "FUTURE_INTERNAL_CODE" not in build_warning_message(
        [{"needs_review": True, "review_reason": "FUTURE_INTERNAL_CODE"}]
    )
    assert build_warning_message([{"needs_review": False}]) is None


@pytest.mark.parametrize("code", list(MODEL_ERROR_LABELS) + list(REVIEW_REASON_LABELS))
def test_saved_messages_are_translated_without_touching_source_ids(code: str) -> None:
    value = humanize_warning_text("Предупреждение: " + code + ".")
    assert code not in value
    assert (REVIEW_REASON_LABELS | MODEL_ERROR_LABELS)[code] in value


def test_report_translates_saved_warning_and_comment(card: dict[str, Any]) -> None:
    card["application"]["warning_message"] = (
        "Для 1 позиций требуется проверка человеком: AI_PRELIMINARY."
    )
    check = card["items"][0]["checks"][1]
    check["review_reason"] = "AI_PRELIMINARY"
    check["comment"] = "Ошибка модели: GIGACHAT_AUTH_FAILED."
    workbook = load_workbook(BytesIO(build_report(card)))
    fields = dict(workbook["Сводка"].values)
    assert "Для 1 позиции" in fields["Предупреждение"]
    text = " ".join(str(cell.value) for sheet in workbook for row in sheet for cell in row)
    assert "AI_PRELIMINARY" not in text and "GIGACHAT_AUTH_FAILED" not in text
    assert check["review_reason"] == "AI_PRELIMINARY"  # Input/machine fields stay intact.


def test_saved_card_formats_warning_without_reanalysis(card: dict[str, Any]) -> None:
    from app.api.applications import _card

    warning = "Для 1 позиций требуется проверка человеком: AI_PRELIMINARY."
    record = SimpleNamespace(**(card["application"] | {"warning_message": warning}))
    record.diagnoses = [SimpleNamespace(name="Диагноз")]
    record.validation_issues = []
    record.items = [
        SimpleNamespace(
            row_number=2,
            drug_name="Препарат",
            mnn="МНН",
            match_confidence=1.0,
            amount=None,
            item_checks=[
                SimpleNamespace(
                    diagnosis=record.diagnoses[0],
                    category=None,
                    source="Источник",
                    reference_version="ref-1",
                    comment="AI_PRELIMINARY.",
                    needs_review=True,
                    review_reason="AI_PRELIMINARY",
                )
            ],
        )
    ]
    presented = _card(record)
    assert "AI_PRELIMINARY" not in presented.application.warning_message
    assert "Для 1 позиции" in presented.application.warning_message
    assert "AI_PRELIMINARY" not in presented.items[0].checks[0].comment
    assert presented.items[0].checks[0].review_reason == "AI_PRELIMINARY"
    assert record.warning_message == warning


def test_translation_preserves_quoted_clinical_text() -> None:
    quote = " Цитата: «Оригинальный текст с SOME_SOURCE_IDENTIFIER» (источник)."
    translated = humanize_warning_text("AI_PRELIMINARY." + quote)
    assert translated.endswith(quote)
    assert translated.startswith(REVIEW_REASON_LABELS["AI_PRELIMINARY"])
