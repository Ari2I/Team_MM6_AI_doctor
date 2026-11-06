"""Проверки содержимого и безопасности Excel-отчёта без БД."""

from io import BytesIO
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.api import applications
from app.config import CATEGORY_LABELS, REVIEW_REASON_LABELS, TRAFFIC_LIGHT_COLORS
from app.main import app
from app.models.enums import ApplicationStatus
from app.schemas.application_api import ApplicationCard
from app.services.report import build_report


@pytest.fixture()
def card() -> dict[str, Any]:
    return {
        "application": {
            "id": str(uuid4()),
            "filename": "sample_ok.xlsx",
            "status": "analyzed",
            "traffic_light": "yellow",
            "requires_review": True,
            "created_at": "2026-11-06T10:00:00+05:00",
            "summary": "Резюме анализа",
            "warning_message": "Нужна проверка",
            "category_shares": {
                "A": 70.0,
                "B": 20.0,
                "V": 5.0,
                "undetermined": 5.0,
            },
        },
        "disclaimer": "Результат носит рекомендательный характер.",
        "diagnoses": ["Диагноз 1", "Диагноз 2"],
        "items": [
            {
                "row_number": 7,
                "drug_name": "Препарат",
                "mnn": "МНН",
                "checks": [
                    {
                        "diagnosis": "Диагноз 1",
                        "category": "A",
                        "needs_review": False,
                        "source": "Правило",
                        "reference_version": "demo-1",
                        "comment": "Комментарий",
                    },
                    {
                        "diagnosis": "Диагноз 2",
                        "needs_review": True,
                        "review_reason": "NO_RULE_FOR_PAIR",
                        "reference_version": "demo-1",
                    },
                ],
            }
        ],
        "issues": [
            {
                "level": "warning",
                "code": "test",
                "message": "Замечание",
                "row": 7,
                "column": "amount",
            }
        ],
    }


def test_report_contents(card: dict[str, Any]) -> None:
    workbook = load_workbook(BytesIO(build_report(ApplicationCard.model_validate(card))))
    assert workbook.sheetnames == ["Сводка", "Препараты", "Предупреждения"]
    summary, drugs, warnings = workbook.worksheets
    assert drugs.max_row - 1 == sum(len(item["checks"]) for item in card["items"])
    assert list(drugs.values)[1] == (
        7,
        "Препарат",
        "МНН",
        "Диагноз 1",
        CATEGORY_LABELS["A"],
        "Проверено правилом",
        None,
        "Правило",
        "demo-1",
        "Комментарий",
    )
    assert drugs["F3"].value == "Требует проверки"
    assert drugs["G3"].value == "Для пары препарат — диагноз нет правила."
    assert all(cell.fill.fgColor.rgb == "00FFF2CC" for cell in drugs[3])
    assert drugs["A2"].fill.patternType is None
    assert drugs.auto_filter.ref == "A1:J3"
    assert list(warnings.values)[1] == ("Предупреждение", "Замечание", 7, "amount")
    fields = dict(summary.values)
    assert fields["Дата создания заявки"] == "2026-11-06T05:00:00+00:00"
    assert fields["Диагнозы заявки"] == "Диагноз 1\nДиагноз 2"
    assert fields["Доля суммы: " + CATEGORY_LABELS["A"]] == 70
    assert fields["Резюме"] == "Резюме анализа"
    assert fields["Предупреждение"] == "Нужна проверка"
    assert fields["Версия справочника"] == "demo-1"
    assert "Демонстрационный (синтетический)" in fields["Справочник"]
    assert fields["Дисклеймер"] == card["disclaimer"]
    for sheet in workbook:
        assert sheet.freeze_panes == "A2"
        assert all(cell.font.bold and cell.fill.patternType == "solid" for cell in sheet[1])
        assert sheet["B2"].alignment.wrap_text
        assert sheet.column_dimensions["B"].width >= 12


@pytest.mark.parametrize("color", TRAFFIC_LIGHT_COLORS)
def test_traffic_colors(card: dict[str, Any], color: str) -> None:
    card["application"]["traffic_light"] = color
    workbook = load_workbook(BytesIO(build_report(card)))
    assert workbook["Сводка"]["B4"].fill.fgColor.rgb == "00" + TRAFFIC_LIGHT_COLORS[color]


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_formula_escaping(card: dict[str, Any], prefix: str) -> None:
    text = prefix + "опасный текст"
    for key in ("filename", "summary", "warning_message"):
        card["application"][key] = text
    card["diagnoses"] = [text]
    card["disclaimer"] = text
    item = card["items"][0]
    item["drug_name"] = item["mnn"] = text
    check = item["checks"][0]
    for key in ("diagnosis", "source", "reference_version", "comment"):
        check[key] = text
    card["issues"][0]["message"] = card["issues"][0]["column"] = text
    workbook = load_workbook(BytesIO(build_report(card)))
    assert workbook["Сводка"]["B2"].value == "'" + text
    for coordinate in ("B2", "C2", "D2", "H2", "I2", "J2"):
        assert workbook["Препараты"][coordinate].value == "'" + text
    assert workbook["Предупреждения"]["B2"].value == "'" + text
    assert workbook["Предупреждения"]["D2"].value == "'" + text
    assert all(cell.data_type != "f" for sheet in workbook for row in sheet for cell in row)


def test_item_without_checks(card: dict[str, Any]) -> None:
    card["items"][0]["checks"] = []
    card["diagnoses"] = []
    card["issues"] = []
    card["application"]["category_shares"] = None
    workbook = load_workbook(BytesIO(build_report(card)))
    drugs = workbook["Препараты"]
    assert drugs.max_row == 2
    assert drugs["B2"].value == "Препарат"
    assert drugs["F2"].value == "Требует проверки"
    assert drugs["D2"].value is None
    assert workbook["Предупреждения"].max_row == 1


@pytest.mark.parametrize("reason", REVIEW_REASON_LABELS)
def test_review_reasons(card: dict[str, Any], reason: str) -> None:
    card["items"][0]["checks"][1]["review_reason"] = reason
    workbook = load_workbook(BytesIO(build_report(card)))
    assert workbook["Препараты"]["G3"].value == REVIEW_REASON_LABELS[reason]


@pytest.mark.parametrize("status", [None, "uploaded", "parsed", "error"])
def test_report_errors(monkeypatch: pytest.MonkeyPatch, status: str | None) -> None:
    application = SimpleNamespace(status=ApplicationStatus(status)) if status else None
    monkeypatch.setattr(applications, "_load_application", lambda *_: application)
    app.dependency_overrides[applications.get_session] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.get(f"/applications/{uuid4()}/report.xlsx")
    finally:
        app.dependency_overrides.pop(applications.get_session, None)
    assert response.status_code == (409 if status else 404)
    error = response.json()["errors"][0]
    assert error["message"] == (
        "Отчёт доступен только после завершения анализа заявки." if status else "Заявка не найдена."
    )
    assert error["row"] is None and error["column"] is None
