"""Границы импорта и сохранение локального резерва."""

from io import BytesIO
from uuid import UUID

from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select

from app.api import applications
from app.models.application import Application, ApplicationItem, ItemCheck
from app.services import application_io
from app.services.analysis import analyze_application
from app.services.rag_adapter import verify_pair
from app.services.reference_seed import load_bundle
from app.services.rag_adapter import get_pipeline
from llm_rag.pipeline import Pipeline
from llm_rag.providers import DemoProvider
from llm_rag.providers.base import ProviderError
from tests.backend.test_demo import EXAMPLE, client


def make_workbook(rows, far_cell=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Торговое наименование", "Диагноз", "Сумма", "Количество"])
    for row in rows:
        sheet.append(row)
    if far_cell:
        sheet["XFD1048576"] = "далеко за пределами таблицы"
    target = BytesIO()
    workbook.save(target)
    return target.getvalue()


def test_sparse_sheet_is_rejected_before_parsing(client, monkeypatch):
    def unexpected_parse(*args, **kwargs):
        raise AssertionError("Лист с чрезмерными координатами дошёл до pandas")

    monkeypatch.setattr(applications, "parse_excel", unexpected_parse)
    content = make_workbook([["Норваск", "Артериальная гипертензия", 100]], far_cell=True)
    response = client.post("/api/applications", files={"file": ("sparse.xlsx", content)})
    assert response.status_code == 413
    assert response.json()["detail"]["issues"][0]["code"] == "workbook_too_large"


def test_missing_amount_and_huge_quantity_keep_purchases(client):
    header = ["Торговое наименование", "Диагноз", "Сумма", "Количество"]
    content = make_workbook([
        ["Норваск", "Артериальная гипертензия; Сахарный диабет 2 типа", "нет суммы", "1e200000"],
        header, ["Глюкофаж", None, 30], ["=1+1", None, 20], ["Глюкофаж", None, 30],
    ])
    response = client.post("/api/applications", files={"file": ("rows.xlsx", content)})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["known_total"] == "80.00"
    assert len(result["items"]) == 4
    assert len(result["checks"]) == 8
    assert result["items"][0]["amount"] is None
    assert result["items"][0]["quantity"] is None
    assert result["items"][0]["original_cells"]["quantity"] == "1e200000"
    assert result["items"][2]["original_cells"]["drug_name"] == "=1+1"
    assert any(i["row"] == 3 and "заголовка" in i["message"] for i in result["issues"])
    assert any(i["code"] == "duplicate_row" for i in result["issues"])
    route = f'/api/applications/{result["application_id"]}'
    book = load_workbook(BytesIO(client.get(route + "/result.xlsx").content))
    formula_cell = book["Заявка"]["B4"]
    assert (formula_cell.value, formula_cell.data_type) == ("=1+1", "s")

    with applications.sessions().begin() as session:
        analyze_application(session, UUID(result["application_id"]), commit=False)
        count = session.scalar(select(func.count()).select_from(ItemCheck)
                               .join(ApplicationItem)
                               .where(ApplicationItem.application_id == UUID(result["application_id"])))
        assert count == 8


def test_provider_failure_preserves_local_result(client, monkeypatch):
    from app.services import rag_adapter

    class FailingProvider:
        def __init__(self, *args, **kwargs):
            assert kwargs.get("verify_ssl_certs", True) is True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def generate(self, payload):
            assert "original_dosage" not in payload["item"]
            assert "amount" not in payload["item"]
            raise ProviderError("GIGACHAT_TLS_ERROR")

    monkeypatch.setattr(rag_adapter, "GigaChatProvider", FailingProvider)
    response = client.post("/api/applications", data={"mode": "gigachat"},
                           files={"file": ("demo.xlsx", EXAMPLE.read_bytes())})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["effective_mode"] == "offline"
    assert result["fallback_reason"] == "GIGACHAT_TLS_ERROR"
    supported = [c for c in result["checks"] if c["evidence_status"] == "SUPPORTED"]
    assert len(supported) == 3
    assert all(c["fallback_reason"] == "GIGACHAT_TLS_ERROR" and c["citations"] for c in supported)
    assert client.get(f'/api/applications/{result["application_id"]}').json() == result


def test_required_fragment_and_version_are_checked():
    bundle = load_bundle()
    pipeline = get_pipeline(bundle.model_dump_json(), "lexical")
    check = ItemCheck(category="A", source="amlodipine-e11-context",
                      reference_version=bundle.reference_version)
    evidence, job = verify_pair(check, inn="Амлодипин", diagnosis="Артериальная гипертензия",
                                bundle=bundle, pipeline=pipeline)
    assert job is None
    assert evidence["reason_code"] == "FRAGMENT_MISMATCH"
    check.reference_version = "old"
    evidence, job = verify_pair(check, inn="Амлодипин", diagnosis="Артериальная гипертензия",
                                bundle=bundle, pipeline=pipeline)
    assert job is None
    assert evidence["reason_code"] == "VERSION_MISMATCH"


def test_gigachat_must_cover_all_claims_of_required_fragment():
    bundle = load_bundle()
    corpus = bundle.corpus.model_copy(deep=True)
    fragment = next(f for f in corpus.fragments if f.fragment_id == "amlodipine-i10")
    fragment.assertions.append(fragment.assertions[0].model_copy(update={
        "claim_id": "second-claim", "quote": "артериальная гипертензия у взрослых",
        "text": "артериальная гипертензия у взрослых",
    }))
    pipeline = Pipeline(corpus, DemoProvider())
    check = ItemCheck(category="A", source=fragment.fragment_id,
                      reference_version=bundle.reference_version)
    _, job = verify_pair(check, inn="Амлодипин", diagnosis="Артериальная гипертензия",
                         bundle=bundle, pipeline=pipeline)
    assert job is not None

    class IncompleteProvider(DemoProvider):
        def generate(self, payload):
            result = super().generate(payload)
            result["claims"] = result["claims"][:1]
            return result

    result = pipeline.process_pair(job.context, job.item, required_fragment_id=job.fragment_id,
                                   provider=IncompleteProvider())
    assert result.explanation_status == "UNCONFIRMED"
    assert "GENERATION_INCOMPLETE_COVERAGE" in result.reasons


def test_failed_analysis_preserves_error_status_without_checks(client, monkeypatch):
    def fail(session, *args, **kwargs):
        raise ValueError('симуляция сбоя анализа')

    monkeypatch.setattr(application_io, 'analyze_application', fail)
    response = client.post('/api/applications', files={'file': ('demo.xlsx', EXAMPLE.read_bytes())})
    assert response.status_code == 503
    application_id = UUID(response.json()['detail']['application_id'])
    with applications.sessions()() as session:
        assert session.get(Application, application_id).status == 'error'
        assert application_io.saved_checks(session, application_id) == []
