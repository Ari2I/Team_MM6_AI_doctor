"""Синтетические заявки: пары, точность чисел, отсутствие подстановок и импортов AI."""

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.config import AISettings
from app.models import Application, ApplicationDiagnosis, ApplicationItem
from app.services.excel_parser import parse_excel
from app.services.integration_mapping import (
    IntegrationMappingError,
    prepare_integration_request,
    validate_integration_request,
)

pytestmark = pytest.mark.usefixtures("no_network")
AI_SOURCE = Path("ai/src").resolve()


def make_application() -> Application:
    return Application(
        id=UUID(int=100),
        age_group=None,
        diagnoses=[
            ApplicationDiagnosis(id=UUID(int=1), name="J45.9 Синтетический диагноз 1"),
            ApplicationDiagnosis(id=UUID(int=2), name="Синтетический диагноз 2"),
        ],
        items=[
            ApplicationItem(
                id=UUID(int=11),
                row_number=2,
                drug_name="Синтетический препарат 1",
                diagnosis="J45.9 Синтетический диагноз 1",
                mnn="Синтетическое вещество",
                dosage="Исходное обозначение",
                quantity=Decimal("2.5"),
                amount=Decimal("900.00"),
            ),
            ApplicationItem(
                id=UUID(int=12),
                row_number=3,
                drug_name="Синтетический препарат 2",
                diagnosis=None,
                dosage=None,
                quantity=None,
                amount=Decimal("100.00"),
            ),
        ],
    )


@pytest.fixture
def application() -> Application:
    return make_application()


@pytest.fixture
def library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    if not AI_SOURCE.is_dir():
        pytest.skip("AI library is absent")
    monkeypatch.syspath_prepend(str(AI_SOURCE))
    monkeypatch.setitem(sys.modules, "gigachat", None)


def test_pairs_follow_existing_rules_without_duplicating_purchases(
    application: Application,
) -> None:
    result = prepare_integration_request(application, currency=AISettings().currency)
    payload = result.payload
    assert payload["request_id"] == str(application.id)
    assert {p["item_id"] for p in payload["purchases"]} == {str(i.id) for i in application.items}
    assert len(payload["purchases"]) == 2 and len(payload["links"]) == 3
    assert {(p["item_id"], p["diagnosis_id"]) for p in payload["links"]} == {
        (str(UUID(int=11)), str(UUID(int=1))),
        (str(UUID(int=12)), str(UUID(int=1))),
        (str(UUID(int=12)), str(UUID(int=2))),
    }
    assert sum(Decimal(p["amount"]) for p in payload["purchases"]) == Decimal(1000)
    first = payload["purchases"][0]
    assert first["quantity"] == "2.5" and first["amount"] == "900.00"
    assert first["dosage"] == application.items[0].dosage
    assert first["inn"] == application.items[0].mnn
    assert result.rows_by_item_id == {str(UUID(int=11)): 2, str(UUID(int=12)): 3}


def test_stable_order_and_no_input_mutation(application: Application) -> None:
    expected = prepare_integration_request(application, currency="EUR")
    application.items.reverse()
    application.diagnoses.reverse()
    before_items = list(application.items)
    before_diagnoses = list(application.diagnoses)
    actual = prepare_integration_request(application, currency="EUR")
    assert actual.payload == expected.payload
    assert application.items == before_items and application.diagnoses == before_diagnoses
    assert application.items[1].quantity == Decimal("2.5")
    assert all(p["currency"] == "EUR" for p in actual.payload["purchases"])


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_row_diagnosis_means_all_application_diagnoses(
    application: Application,
    value: str | None,
) -> None:
    application.items[0].diagnosis = value
    payload = prepare_integration_request(application, currency="RUB").payload
    assert len(payload["links"]) == 4 and len(payload["purchases"]) == 2


@pytest.mark.parametrize("group", ["adult", "child"])
def test_age_group_is_retained_and_cannot_be_silently_dropped(
    application: Application,
    group: str,
) -> None:
    application.age_group = group
    prepared = prepare_integration_request(application, currency="RUB")
    assert prepared.age_group == group
    assert "age_years" not in json.dumps(prepared.payload)
    assert "age_group" not in json.dumps(prepared.payload)
    with pytest.raises(IntegrationMappingError) as caught:
        validate_integration_request(prepared)
    assert caught.value.column == "age_group"
    assert caught.value.review_reason == "AI_UNAVAILABLE"
    assert application.age_group == group


@pytest.mark.parametrize(
    "name,code",
    [
        ("j45.9 Синтетический диагноз", "J45.9"),
        (" J45: Синтетический диагноз", "J45"),
        ("J45.9", "J45.9"),
        ("Синтетический диагноз J45.9", None),
        ("J45.99999 Синтетический диагноз", None),
        ("J45.9suffix", None),
        ("А00 Синтетический диагноз", None),  # Кириллическая А не заменяется латинской.
    ],
)
def test_only_explicit_leading_icd_code_is_extracted(
    application: Application,
    name: str,
    code: str | None,
) -> None:
    application.diagnoses[0].name = name
    application.items[0].diagnosis = name
    payload = prepare_integration_request(application, currency="RUB").payload
    assert payload["diagnoses"][0]["name"] == name
    assert payload["diagnoses"][0].get("icd10") == code


@pytest.mark.parametrize("currency", ["", "rub", "RUB\n", "PRIVATE-VALUE"])
def test_invalid_currency_is_safe(application: Application, currency: str) -> None:
    with pytest.raises(IntegrationMappingError) as caught:
        prepare_integration_request(application, currency=currency)
    assert caught.value.column == "currency" and caught.value.row is None
    if currency:
        assert currency not in str(caught.value)


@pytest.mark.parametrize("relation", ["items", "diagnoses"])
def test_empty_lists_are_not_filled(application: Application, relation: str) -> None:
    setattr(application, relation, [])
    with pytest.raises(IntegrationMappingError):
        prepare_integration_request(application, currency="RUB")


@pytest.mark.parametrize("target", ["application", "item", "diagnosis"])
def test_unsaved_ids_are_not_generated(application: Application, target: str) -> None:
    obj = {
        "application": application,
        "item": application.items[0],
        "diagnosis": application.diagnoses[0],
    }[target]
    obj.id = None
    with pytest.raises(IntegrationMappingError) as caught:
        prepare_integration_request(application, currency="RUB")
    assert caught.value.column == "id" and obj.id is None


@pytest.mark.parametrize("relation", ["items", "diagnoses"])
def test_duplicate_ids_are_rejected(application: Application, relation: str) -> None:
    values = getattr(application, relation)
    values[1].id = values[0].id
    with pytest.raises(IntegrationMappingError):
        prepare_integration_request(application, currency="RUB")


def test_unresolved_diagnosis_is_not_relinked_or_leaked(application: Application) -> None:
    marker = "PRIVATE-DIAGNOSIS-MARKER"
    application.items[0].diagnosis = marker
    with pytest.raises(IntegrationMappingError) as caught:
        prepare_integration_request(application, currency="RUB")
    assert caught.value.row == 2 and caught.value.column == "diagnosis"
    assert caught.value.review_reason == "AI_INPUT_INCOMPLETE"
    assert marker not in str(caught.value) and marker not in repr(caught.value)
    assert application.items[0].diagnosis == marker


def test_missing_name_is_not_invented(application: Application) -> None:
    application.items[0].drug_name = None
    with pytest.raises(IntegrationMappingError) as caught:
        prepare_integration_request(application, currency="RUB")
    assert caught.value.row == 2 and caught.value.column == "drug_name"
    assert application.items[0].drug_name is None


def test_duplicate_diagnosis_names_are_not_merged(application: Application) -> None:
    application.diagnoses[1].name = application.diagnoses[0].name
    with pytest.raises(IntegrationMappingError) as caught:
        prepare_integration_request(application, currency="RUB")
    assert caught.value.column == "diagnosis"


def test_payload_is_not_in_object_repr(application: Application) -> None:
    application.items[0].drug_name = "PRIVATE-TRADE-MARKER"
    prepared = prepare_integration_request(application, currency="RUB")
    assert "PRIVATE-TRADE-MARKER" not in repr(prepared)
    assert str(application.id) not in repr(prepared)


def test_local_payload_contains_only_contract_fields(application: Application) -> None:
    application.filename = "PRIVATE-FILENAME-MARKER.xlsx"
    application.summary = "PRIVATE-SUMMARY-MARKER"
    prepared = prepare_integration_request(application, currency="RUB")
    assert set(prepared.payload) == {
        "schema_version",
        "request_id",
        "purchases",
        "diagnoses",
        "links",
    }
    for purchase in prepared.payload["purchases"]:
        assert set(purchase) == {
            "item_id",
            "name",
            "inn",
            "dosage",
            "quantity",
            "amount",
            "currency",
        }
    assert "PRIVATE-FILENAME-MARKER" not in json.dumps(prepared.payload)
    assert "PRIVATE-SUMMARY-MARKER" not in json.dumps(prepared.payload)


def test_mapping_and_missing_library_in_fresh_process() -> None:
    script = """
import sys,tempfile
from pathlib import Path
sys.modules['llm_rag'] = None
sys.modules['gigachat'] = None
from pytest import MonkeyPatch
from tests.conftest import no_network
patch=MonkeyPatch()
guard=no_network.__wrapped__(patch,Path(tempfile.mkdtemp()))
next(guard)
try:
 from tests.test_integration_mapping import make_application
 from app.services.integration_mapping import (
  prepare_integration_request,validate_integration_request,IntegrationMappingError)
 prepared=prepare_integration_request(make_application(),currency='RUB')
 assert len(prepared.payload['links'])==3
 try:
  validate_integration_request(prepared)
 except IntegrationMappingError as error:
  assert error.review_reason=='AI_UNAVAILABLE'
 else:
  raise AssertionError('Missing library was ignored')
 assert not any(name.startswith(('llm_rag.','gigachat.')) for name in sys.modules)
finally:
 try: next(guard)
 except StopIteration: pass
 patch.undo()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, "Подготовка без AI-библиотеки или запрет сети нарушены."


def test_missing_purchase_values_remain_missing(
    application: Application,
    library_path: None,
) -> None:
    item = application.items[0]
    item.dosage = item.quantity = item.amount = item.mnn = None
    prepared = prepare_integration_request(application, currency="RUB")
    payload = validate_integration_request(prepared)
    purchase = payload["purchases"][0]
    assert all(purchase[field] is None for field in ("dosage", "quantity", "amount", "inn"))
    assert item.dosage is None and item.quantity is None and item.amount is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", Decimal(0)),
        ("quantity", Decimal(-1)),
        ("quantity", Decimal("NaN")),
        ("amount", Decimal("Infinity")),
        ("amount", Decimal(-1)),
        ("amount", Decimal("1000000000000")),
        ("amount", Decimal("1.0001")),
    ],
)
def test_incompatible_values_are_not_dropped_or_rounded(
    application: Application,
    library_path: None,
    field: str,
    value: Decimal,
) -> None:
    item = application.items[0]
    setattr(item, field, value)
    with pytest.raises(IntegrationMappingError) as caught:
        prepared = prepare_integration_request(application, currency="RUB")
        validate_integration_request(prepared)
    assert caught.value.row == 2 and caught.value.column == field
    assert caught.value.review_reason == "AI_INPUT_INCOMPLETE"
    assert "input_value" not in str(caught.value)
    assert getattr(item, field) is value


@pytest.mark.parametrize("filename", ["correct", "typo", "unknown", "two_diagnoses"])
def test_demo_workbooks_validate_with_real_library(
    filename: str,
    library_path: None,
) -> None:
    from llm_rag.integration_contracts import IntegrationRequest
    from llm_rag.integration_policy import purchase_totals

    parsed = parse_excel(Path("data/demo") / (filename + ".xlsx"))
    names = list(dict.fromkeys(item.diagnosis for item in parsed.items if item.diagnosis))
    application = Application(
        id=UUID(int=100),
        age_group=None,
        diagnoses=[
            ApplicationDiagnosis(id=UUID(int=i), name=name) for i, name in enumerate(names, 1)
        ],
        items=[
            ApplicationItem(id=UUID(int=i), **item.model_dump())
            for i, item in enumerate(parsed.items, 10)
        ],
    )
    prepared = prepare_integration_request(application, currency="RUB")
    payload = validate_integration_request(prepared)
    request = IntegrationRequest.model_validate(payload)
    expected = {
        (str(item.id), str(diagnosis.id))
        for item in application.items
        for diagnosis in application.diagnoses
        if not item.diagnosis or item.diagnosis == diagnosis.name
    }
    assert {(p.item_id, p.diagnosis_id) for p in request.links} == expected
    assert len(request.purchases) == len(parsed.items)
    assert purchase_totals(request).totals == {"RUB": Decimal(1000)}
    assert "gigachat" not in sys.modules or sys.modules["gigachat"] is None
