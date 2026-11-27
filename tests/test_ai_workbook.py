"""Приём AI-книг без сети и SDK; повторная сборка только из сохранённого входа."""

import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.engine import default

from app.config import AISettings
from app.db.types import ExactAmount
from app.services import integration_client
from app.services.integration_client import IntegrationWorkerClient
from app.services.integration_mapping import diagnoses_for_item, prepare_integration_request
from app.services.integration_upload import IntegrationUploadError, application_from_ai_workbook
from tests.ai_workbook_data import workbook_bytes

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def library_path(monkeypatch):
    source = Path("ai/src").resolve()
    if not source.is_dir():
        pytest.skip("AI library is absent")
    monkeypatch.syspath_prepend(str(source))
    monkeypatch.setitem(sys.modules, "gigachat", None)


@pytest.mark.parametrize("layout", [2, 4])
def test_persistable_input_preserves_age_edition_money_and_explicit_links(library_path, layout):
    application = application_from_ai_workbook(
        workbook_bytes(layout),
        filename="synthetic.xlsx",
        mode="network",
        age_group="adult",
    )
    assert application is not None
    saved = deepcopy(application.integration_input)
    assert saved["diagnoses"][0]["conditions"]["age_years"] == "38"
    assert saved["diagnoses"][0]["conditions"]["age_group"] == "adult"
    assert saved["diagnoses"][0]["recommendation_edition"] == "359_3"
    assert application.items[0].row_number == 3
    assert application.items[0].amount == Decimal("100.125")
    assert len(saved["links"]) == (2 if layout == 2 else 3)
    actual = prepare_integration_request(application, currency="EUR")
    assert actual.payload == saved  # Валюта книги, не настройка плоского Excel.
    application.items.reverse()
    application.diagnoses.reverse()
    assert prepare_integration_request(application, currency="RUB").payload == saved
    assert application.integration_input == saved
    second = next(i for i in application.items if i.row_number == 4)
    assert len(diagnoses_for_item(application, second)) == 1


@pytest.mark.parametrize("layout", [2, 4])
@pytest.mark.parametrize("mode", ["template", "offline"])
def test_local_modes_reject_books_before_ai_import(monkeypatch, mode, layout):
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    monkeypatch.setitem(sys.modules, "gigachat", None)
    with pytest.raises(IntegrationUploadError, match="сетевой режим"):
        application_from_ai_workbook(workbook_bytes(layout), filename="synthetic.xlsx", mode=mode)


@pytest.mark.parametrize(
    "options",
    [
        {"age": "8", "group": "adult"},
        {"links": [["unknown", "d1"]]},
        {"links": [["i1", "d1"], ["i1", "d1"]]},
        {"formula": True},
    ],
)
def test_invalid_books_have_safe_error_without_cell_contents(library_path, options):
    with pytest.raises(IntegrationUploadError) as caught:
        application_from_ai_workbook(
            workbook_bytes(**options), filename="private-name.xlsx", mode="network"
        )
    assert "private-name" not in str(caught.value)
    assert "Синтетический" not in str(caught.value)


def test_form_cannot_silently_override_book_age_or_links(library_path):
    with pytest.raises(IntegrationUploadError):
        application_from_ai_workbook(
            workbook_bytes(), filename="synthetic.xlsx", mode="network", age_group="child"
        )
    with pytest.raises(IntegrationUploadError):
        application_from_ai_workbook(
            workbook_bytes(), filename="synthetic.xlsx", mode="network", diagnoses=["Other"]
        )


def test_legacy_flat_input_does_not_need_ai(monkeypatch):
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    assert (
        application_from_ai_workbook(
            Path("data/demo/correct.xlsx").read_bytes(),
            filename="synthetic.xlsx",
            mode="template",
        )
        is None
    )


def test_money_type_preserves_three_decimals_and_legacy_scale():
    kind = ExactAmount()
    dialect = default.DefaultDialect()
    assert kind.process_result_value(Decimal("100.125"), dialect) == Decimal("100.125")
    assert str(kind.process_result_value(Decimal("200"), dialect)) == "200.00"


@pytest.mark.parametrize("layout", [2, 4])
def test_book_passes_actual_worker_protocol_without_network(library_path, monkeypatch, layout):
    application = application_from_ai_workbook(
        workbook_bytes(layout),
        filename="synthetic.xlsx",
        mode="network",
    )
    original_run = integration_client.subprocess.run
    calls = []

    def run(command, **kwargs):
        assert command == [sys.executable, "-m", "app.services.integration_worker"]
        calls.append(True)
        return original_run([sys.executable, "-m", "tests.synthetic_integration_worker"], **kwargs)

    monkeypatch.setattr(integration_client.subprocess, "run", run)
    reply = IntegrationWorkerClient(AISettings(mode="network")).analyze(
        prepare_integration_request(application, currency="RUB"),
    )
    assert calls == [True]
    assert reply.outcome == "result" and reply.error is None
    assert len(reply.result["pairs"]) == len(application.integration_input["links"])
    assert all(p["decision"]["proposed_category"] == "А" for p in reply.result["pairs"])
    assert all(p["generation_mode"] == "MODEL" for p in reply.result["pairs"])
    assert reply.result["request"] == application.integration_input
