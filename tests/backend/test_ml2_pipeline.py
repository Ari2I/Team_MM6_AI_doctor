"""Локальные проверки поиска КР и сохранения отдельного результата ML-2."""

import base64
import gzip
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.models.enums import DrugCategory
from app.services.ml2_classifier import EvidenceFragment
from app.services.ml2_clinicalrecs_source import (
    CachedRecommendation,
    CatalogCandidate,
    ClinicalRecommendation,
    fragments_for_inn,
    parse_recommendation,
    recommendations_for_diagnosis,
)
from app.services.ml2_pipeline import classify_official_application
from app.services.official_reference import OfficialIndex
from tests.backend.test_official_reference import make_reference


def _recommendation_html() -> str:
    return """<!doctype html><html><body>
    <h1>Тестовая рекомендация</h1>
    <dl class="krt-pass">
      <dt>ID:</dt><dd>388_3</dd>
      <dt>Кодирование по МКБ-10:</dt><dd>K25</dd>
      <dt>Возрастная категория:</dt><dd>Взрослые, дети</dd>
      <dt>Год утверждения:</dt><dd>2024</dd>
      <dt>Статус:</dt><dd>Действует</dd>
      <dt>Статус применения:</dt><dd>Применяется</dd>
    </dl>
    <p class="krt-srcl">Источник: Рубрикатор Минздрава России</p>
    <a href="https://cr.minzdrav.gov.ru/preview-cr/388_3">Оригинал</a>
    <h2 id="s3">3. Лечение</h2>
    <h3 id="s3-h1">3.1 Медикаментозная терапия</h3>
    <ul id="r3-1" class="krt-r">
      <li>Омепразол рекомендуется как терапия при условиях из рекомендации.</li>
    </ul>
    <h2 id="s4">4. Реабилитация</h2>
    </body></html>"""


def test_treatment_parser_keeps_source_identity_and_exact_quote():
    url = "https://xn--80ahcaaarabfanddhyjjq1br6ji.xn--p1ai/kr/test-388"
    recommendation = parse_recommendation(_recommendation_html(), url)
    fragments = fragments_for_inn(recommendation, "омепразол")

    assert recommendation.edition_id == "388_3"
    assert recommendation.icd10 == ("K25",)
    assert recommendation.is_current_ministry_copy
    assert len(fragments) == 1
    assert "Омепразол рекомендуется" in fragments[0].text
    assert fragments[0].url == "https://cr.minzdrav.gov.ru/preview-cr/388_3"
    assert fragments[0].copy_url == url


def test_catalog_search_reads_specific_icd_codes_from_ru_catalog(tmp_path):
    data = {
        "RU": [
            [
                "Тестовая рекомендация",
                "388_3",
                "2024-09-30",
                3,
                3,
                [],
                "K25",
                [],
                [],
                "K25.1,K25.2",
                "",
            ]
        ],
        "INT": [],
        "SOC": [],
    }
    packed = base64.b64encode(gzip.compress(json.dumps(data).encode("utf-8"))).decode("ascii")
    chunks = [
        packed[index * len(packed) // 11 : (index + 1) * len(packed) // 11] for index in range(11)
    ]
    scripts = "".join(
        f'<script id="krdata{index}">{chunk}</script>' for index, chunk in enumerate(chunks)
    )
    html = f'<script>const KRP = {{"388":"test-388"}}; const krPage = x => x;</script>{scripts}'
    (tmp_path / "catalog-ru.json").write_text(
        json.dumps(
            {
                "checked_at": datetime.now(UTC).isoformat(),
                "html": html,
            }
        ),
        encoding="utf-8",
    )

    candidates = recommendations_for_diagnosis("K25.1", cache_dir=tmp_path)

    assert len(candidates) == 1
    assert candidates[0].edition_id == "388_3"
    assert "K25.1" in candidates[0].icd10
    assert candidates[0].page_url.endswith("/kr/test-388")


def test_ambiguous_guideline_is_saved_without_overwriting_official_category(monkeypatch):
    from app.models import Application, ApplicationDiagnosis, ApplicationItem, ItemCheck

    application_id, diagnosis_id, item_id, check_id = (uuid4() for _ in range(4))
    diagnosis = SimpleNamespace(id=diagnosis_id, name="Тестовый диагноз")
    item = SimpleNamespace(
        id=item_id,
        row_number=1,
        drug_name="Независимый тестовый препарат",
        dosage="таблетки",
        mnn="компонент а + компонент б",
    )
    check = SimpleNamespace(
        id=check_id,
        item_id=item_id,
        diagnosis_id=diagnosis_id,
        category=DrugCategory.A,
        evidence={"evidence_status": "SUPPORTED", "review_sources": []},
    )
    application = SimpleNamespace(id=application_id, demo_meta={"analysis_run_id": "run-1"})

    candidates = (
        CatalogCandidate(
            "Взрослая КР",
            "277_2",
            "2025-01-01",
            "Взрослые",
            ("G43.9",),
            "https://example.test/1",
            "https://cr.minzdrav.gov.ru/preview-cr/277_2",
        ),
        CatalogCandidate(
            "Детская КР",
            "388_3",
            "2024-01-01",
            "Дети",
            ("G43.9",),
            "https://example.test/2",
            "https://cr.minzdrav.gov.ru/preview-cr/388_3",
        ),
    )
    monkeypatch.setattr(
        "app.services.ml2_pipeline.recommendations_for_diagnosis", lambda _code: candidates
    )

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, model, _identity, **_kwargs):
            assert model is Application
            return application

        def scalars(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            return iter(
                {ApplicationDiagnosis: [diagnosis], ApplicationItem: [item], ItemCheck: [check]}[
                    entity
                ]
            )

    class SessionFactory:
        def __call__(self):
            return Session()

        def begin(self):
            return Session()

    result = classify_official_application(
        SessionFactory(),
        application_id,
        "run-1",
        OfficialIndex(make_reference()),
    )

    assert result["pairs"] == 1
    assert check.category == DrugCategory.A
    assert check.evidence["ml2_classification"]["status"] == "NEEDS_CONTEXT"
    assert check.evidence["ml2_classification"]["proposed_category"] is None
    assert check.evidence["ml2_classification"]["missing_context"] == [
        "Возрастная группа",
        "Редакция КР",
    ]


def test_successful_classification_persists_citations_and_preserves_rule(monkeypatch):
    from app.models import Application, ApplicationDiagnosis, ApplicationItem, ItemCheck
    from app.schemas.demo_result import ML2Classification

    application_id, diagnosis_id, item_id, check_id = (uuid4() for _ in range(4))
    page_url = "https://xn--80ahcaaarabfanddhyjjq1br6ji.xn--p1ai/kr/test-388"
    primary_url = "https://cr.minzdrav.gov.ru/preview-cr/388_3"
    quote = "Омепразол рекомендуется для лечения тестового заболевания."
    fragment = EvidenceFragment(
        fragment_id="treatment-1",
        source_id="kr-388-3",
        title="Тестовая рекомендация",
        version="КР 388_3, 2024",
        url=primary_url,
        location="Раздел 3. Лечение / рекомендация 1",
        text=quote,
        copy_url=page_url,
    )
    application = SimpleNamespace(
        id=application_id,
        demo_meta={"analysis_run_id": "run-1", "item_blocks": {}, "diagnosis_blocks": {}},
    )
    diagnosis = SimpleNamespace(id=diagnosis_id, name="K25.9 Тестовый диагноз")
    item = SimpleNamespace(
        id=item_id,
        row_number=1,
        drug_name="Независимый тестовый препарат",
        dosage="20 мг",
        mnn="омепразол",
    )
    check = SimpleNamespace(
        id=check_id,
        item_id=item_id,
        diagnosis_id=diagnosis_id,
        category=DrugCategory.A,
        evidence={"evidence_status": "SUPPORTED", "review_sources": []},
    )
    candidate = CatalogCandidate(
        "Тестовая рекомендация",
        "388_3",
        "2024-09-30",
        "Взрослые, дети",
        ("K25",),
        page_url,
        primary_url,
    )
    recommendation = ClinicalRecommendation(
        page_url=page_url,
        title="Тестовая рекомендация",
        edition_id="388_3",
        year="2024",
        population="Взрослые, дети",
        icd10=("K25",),
        status="Действует",
        application_status="Применяется",
        source_label="Источник: Рубрикатор Минздрава России",
        primary_url=primary_url,
        segments=(),
        retrieved_at=datetime.now(UTC).isoformat(),
    )

    monkeypatch.setattr(
        "app.services.ml2_pipeline.recommendations_for_diagnosis",
        lambda _code: (candidate,),
    )
    monkeypatch.setattr(
        "app.services.ml2_pipeline.fetch_recommendation",
        lambda _url: CachedRecommendation(
            recommendation, "DOWNLOADED", datetime.now(UTC).isoformat()
        ),
    )
    monkeypatch.setattr(
        "app.services.ml2_pipeline.fragments_for_inn",
        lambda _recommendation, _inn: (fragment,),
    )
    monkeypatch.setattr(
        "app.services.ml2_pipeline.gigachat_settings",
        lambda: {
            "GIGACHAT_CREDENTIALS": "synthetic-test-credentials",
            "GIGACHAT_ACCESS_TOKEN": None,
            "GIGACHAT_MODEL": "test-model",
            "GIGACHAT_CA_BUNDLE": None,
        },
    )

    class FakeProvider:
        last_total_tokens = 7

        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def complete_json(self, _payload, _system, _schema):
            return {
                "assessment": "CORE_TREATMENT",
                "conditions": [],
                "missing_context": [],
                "evidence": [{"fragment_id": fragment.fragment_id, "quote": quote}],
            }

    monkeypatch.setattr("app.services.ml2_pipeline.GigaChatProvider", FakeProvider)

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, model, _identity, **_kwargs):
            assert model is Application
            return application

        def scalars(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            rows = {
                ApplicationDiagnosis: [diagnosis],
                ApplicationItem: [item],
                ItemCheck: [check],
            }.get(entity, [check])
            return iter(rows)

    class SessionFactory:
        def __call__(self):
            return Session()

        def begin(self):
            return Session()

    snapshot = make_reference().model_copy(update={
        "registrations": [
            make_reference().registrations[0].model_copy(update={"mnn": "омепразол"})
        ]
    })
    result = classify_official_application(
        SessionFactory(), application_id, "run-1", OfficialIndex(snapshot)
    )

    assert result == {"attempted": True, "model_calls": 1, "successful": 1, "tokens": 7, "pairs": 1}
    assert check.category == DrugCategory.A
    parsed = ML2Classification.model_validate(check.evidence["ml2_classification"])
    assert parsed.status == "PROPOSED"
    assert parsed.proposed_category == "А"
    assert parsed.approved_rule_preserved is True
    assert parsed.citations[0].quote == quote
    assert parsed.citations[0].url == primary_url
    assert parsed.dosage_check is not None
    assert parsed.dosage_check.status == "INSUFFICIENT_DATA"
    assert "Частота приёма" in parsed.dosage_check.missing_data
    assert check.evidence["review_sources"][0]["fragment_id"] == fragment.fragment_id
    assert application.demo_meta["ml2_status_counts"] == {"PROPOSED": 1}
