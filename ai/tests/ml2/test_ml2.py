"""Проверки только нового ML-2 пакета, без backend, БД и внешних запросов."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from llm_rag.contracts import AnalysisRequest
from llm_rag.corpus import Corpus
from llm_rag.ml2 import ML2Pipeline, RegistrySnapshot
from llm_rag.ml2.classifier import (
    ClassificationRequest,
    EvidenceFragment,
    classify_pair,
    gigachat_completer,
)
from llm_rag.ml2.clinicalrecs import (
    BASE_URL,
    CachedRecommendation,
    CatalogCandidate,
    ClinicalRecommendation,
    TreatmentSegment,
    fragments_for_inn,
    parse_recommendation,
)
from llm_rag.ml2.registry import (
    RegistryEntry,
    RegistryIndex,
    RegistrySource,
    build_snapshot,
)
from llm_rag.pipeline import Pipeline
from llm_rag.providers import DemoProvider

ROOT = Path(__file__).resolve().parents[2]
QUOTE = "Тестовое вещество применяется для снятия симптомов."


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Test must not perform HTTP requests")

    monkeypatch.setattr(httpx.Client, "send", deny)


@pytest.fixture
def source():
    return RegistrySource(
        title="Синтетический реестр",
        url="https://grls.rosminzdrav.ru/GRLS.aspx",
        version="synthetic-1",
        retrieved_at=date(2026, 11, 10),
        location="строка 1",
        quote="Тестовое вещество",
    )


@pytest.fixture
def registry(source):
    return RegistrySnapshot(
        version="synthetic",
        as_of=date(2026, 11, 10),
        registrations=(
            RegistryEntry(
                registration="TEST",
                trade_name="Тестовый препарат",
                mnn="Тестовое вещество",
                source=source,
            ),
        ),
    )


@pytest.fixture
def purchase_request():
    return AnalysisRequest.model_validate(
        {
            "schema_version": "1.0",
            "request_id": "synthetic",
            "diagnosis": "J45.9 Тестовый диагноз",
            "is_demo": True,
            "context": {},
            "items": [
                {
                    "item_id": "row-1",
                    "original": {
                        "trade_name": "Тестовый препарат",
                        "dosage": "10 мг",
                        "quantity": 3,
                        "amount": "123.45",
                        "currency": "RUB",
                    },
                    "inn": None,
                    "category": None,
                    "classification_reason": None,
                    "dosage_check": {
                        "status": "NOT_CHECKED",
                        "reason": None,
                        "missing_data": [],
                    },
                    "source_refs": [],
                    "upstream_status": "PARTIAL",
                    "missing_data": [],
                }
            ],
        }
    )


def fragment():
    return EvidenceFragment(
        "f1",
        "s1",
        "Синтетический фрагмент",
        "1",
        "https://cr.minzdrav.gov.ru/preview-cr/1_1",
        "Лечение, абзац 1",
        QUOTE,
    )


def complete(system, user):
    return {
        "assessment": "SUPPORTIVE_TREATMENT",
        "conditions": [],
        "missing_context": [],
        "evidence": [{"fragment_id": "f1", "quote": QUOTE}],
    }


def test_registry_ambiguity_and_strength(source):
    snapshot = RegistrySnapshot(
        version="synthetic",
        as_of=date(2026, 11, 10),
        registrations=tuple(
            RegistryEntry(
                registration=str(i),
                trade_name="Препарат®",
                mnn=inn,
                form=form,
                source=source,
            )
            for i, (inn, form) in enumerate(
                (("вещество а", "10 мг"), ("вещество б", "20 мг"))
            )
        ),
    )
    index = RegistryIndex(snapshot)
    assert index.resolve(" препарат ").mnn is None
    assert index.resolve("ПРЕПАРАТ™", "20 мг").mnn == "вещество б"
    assert index.resolve("ПрепораТ").mnn is None


def test_csv_import_omits_inactive_and_future_registrations(tmp_path):
    path = tmp_path / "grls.csv"
    path.write_text(
        "regnumber;tradename;internationalname;статус;regdate\n"
        "1;Тест;вещество;действующий;2020-01-01\n"
        "2;Отменён;вещество;аннулирован;2020-01-01\n"
        "3;Будущий;вещество;действующий;2030-01-01\n",
        encoding="utf-8",
    )
    snapshot = build_snapshot(
        path, date(2026, 11, 10), registry_url="https://grls.rosminzdrav.ru/GRLS.aspx"
    )
    assert [r.registration for r in snapshot.registrations] == ["1"]
    assert "строка 2" in snapshot.registrations[0].source.location


def test_classification_preserves_row_and_uses_verified_quote(
    purchase_request, registry
):
    before = purchase_request.model_dump()
    batch = ML2Pipeline(
        registry,
        complete_json=complete,
        fragments_by_inn={"тестовое вещество": (fragment(),)},
    ).run(purchase_request)
    item = batch.request.items[0]
    assert item.category == "Б"
    assert item.original.model_dump() == purchase_request.items[0].original.model_dump()
    assert (
        item.original.amount == purchase_request.items[0].original.amount
    )  # Не умножается на quantity.
    assert item.source_refs == ["f1"]
    assert item.dosage_check.status == "INSUFFICIENT_DATA"
    assert batch.evaluations[0].classification.citations[0].quote == QUOTE
    assert purchase_request.model_dump() == before
    AnalysisRequest.model_validate_json(batch.request.model_dump_json())
    json.dumps(batch.details(), ensure_ascii=False)


@pytest.mark.parametrize(
    "assessment,quote,status,category",
    [
        ("SUPPORTIVE_TREATMENT", "Выдуманная цитата", "INVALID_RESPONSE", None),
        ("CORE_TREATMENT", QUOTE, "NEEDS_REVIEW", None),
        ("EXPLICITLY_NOT_RECOMMENDED", QUOTE, "NEEDS_REVIEW", None),
    ],
)
def test_model_cannot_invent_citation_or_gold_standard(
    assessment, quote, status, category
):
    result = classify_pair(
        ClassificationRequest(
            "r", "i", "J45.9", "тестовое вещество", (fragment(),), {}
        ),
        lambda *_: {
            "assessment": assessment,
            "conditions": [],
            "missing_context": [],
            "evidence": [{"fragment_id": "f1", "quote": quote}],
        },
    )
    assert result.status == status
    assert result.proposed_category == category


def test_missing_unknown_combination_and_offline_are_explicit(
    purchase_request, registry, source
):
    purchase_request.items.append(
        purchase_request.items[0].model_copy(update={"item_id": "row-2"}, deep=True)
    )
    purchase_request.items[0].original.trade_name = "Несуществующий"
    batch = ML2Pipeline(registry).run(purchase_request)
    assert [v.classification.status for v in batch.evaluations] == [
        "INN_UNRESOLVED",
        "NO_EVIDENCE",
    ]
    assert all(i.category is None for i in batch.request.items)
    combined = registry.model_copy(
        update={
            "registrations": (
                RegistryEntry(
                    registration="2",
                    trade_name="Тестовый препарат",
                    mnn="а + б",
                    source=source,
                ),
            )
        }
    )
    assert (
        ML2Pipeline(combined).run(purchase_request).evaluations[1].classification.status
        == "MULTI_INGREDIENT_REVIEW"
    )


def test_prepared_request_works_in_unchanged_rag_pipeline(source):
    purchase_request = AnalysisRequest.model_validate_json(
        (ROOT / "examples/request.json").read_bytes()
    )
    corpus = Corpus.model_validate_json((ROOT / "examples/corpus.json").read_bytes())
    registry = RegistrySnapshot(
        version="synthetic",
        as_of=date(2026, 11, 10),
        registrations=tuple(
            RegistryEntry(
                registration=str(i),
                trade_name=item.original.trade_name,
                mnn=item.inn,
                source=source,
            )
            for i, item in enumerate(purchase_request.items)
            if item.inn
        ),
    )
    batch = ML2Pipeline(registry, approved_corpus=corpus).run(purchase_request)
    assert batch.request.items[0].category == "А"
    assert batch.evaluations[0].rule_applied
    assert (
        batch.request.items[0].classification_reason
        == purchase_request.items[0].classification_reason
    )
    result = Pipeline(corpus, DemoProvider()).run(batch.request)
    assert len(result.items) == len(purchase_request.items)
    assert result.costs_by_currency[0].total_amount == sum(
        i.original.amount for i in purchase_request.items
    )
    assert result.items[0].category == "А"
    assert (
        result.items[0].explanation_status == "INSUFFICIENT_DATA"
    )  # Дозировка остаётся непроверенной.
    assert result.items[0].claims


def candidate(population="Взрослые, дети", **kwargs):
    return CatalogCandidate(
        "Синтетическая КР",
        "1_1",
        "2024",
        population,
        ("J45",),
        BASE_URL + "/kr/test-1",
        "https://cr.minzdrav.gov.ru/preview-cr/1_1",
        **kwargs,
    )


def recommendation(c):
    return ClinicalRecommendation(
        c.page_url,
        c.title,
        c.edition_id,
        "2024",
        c.population,
        c.icd10,
        "Действует",
        "Применяется",
        "Рубрикатор Минздрава России",
        c.primary_url,
        (TreatmentSegment("Лечение", QUOTE),),
        "2026-11-10T10:00:00+00:00",
    )


class FakeSources:
    def __init__(self, candidates, state="FRESH_CACHE", mismatch=False):
        self.values, self.state, self.mismatch = candidates, state, mismatch
        self.loaded = 0

    def candidates(self, code, age):
        return self.values

    def load(self, c):
        self.loaded += 1
        rec = recommendation(c)
        if self.mismatch:
            rec = replace(rec, edition_id="2_1")
        return CachedRecommendation(rec, self.state, "2026-11-10T10:00:00+00:00")


@pytest.mark.parametrize(
    "values,state,mismatch,status",
    [
        ([candidate(), candidate("Дети")], "FRESH_CACHE", False, "NEEDS_CONTEXT"),
        ([candidate("Дети")], "FRESH_CACHE", False, "NEEDS_CONTEXT"),
        ([candidate(cache_state="STALE_CACHE")], "FRESH_CACHE", False, "NEEDS_REVIEW"),
        ([candidate()], "STALE_FALLBACK", False, "NEEDS_REVIEW"),
        ([candidate()], "FRESH_CACHE", True, "NEEDS_REVIEW"),
    ],
)
def test_source_ambiguity_age_staleness_and_identity(
    purchase_request, registry, values, state, mismatch, status
):
    sources = FakeSources(values, state, mismatch)
    batch = ML2Pipeline(registry, complete_json=complete, sources=sources).run(
        purchase_request, live_sources=True
    )
    assert batch.evaluations[0].classification.status == status
    assert batch.request.items[0].category is None


def test_live_source_can_supply_fragments_without_demo(purchase_request, registry):
    c = candidate()
    rec_fragment = fragments_for_inn(recommendation(c), "Тестовое вещество")[0]

    def model(*_):
        return {
            "assessment": "SUPPORTIVE_TREATMENT",
            "conditions": [],
            "missing_context": [],
            "evidence": [{"fragment_id": rec_fragment.fragment_id, "quote": QUOTE}],
        }

    batch = ML2Pipeline(registry, complete_json=model, sources=FakeSources([c])).run(
        purchase_request, live_sources=True
    )
    assert batch.request.items[0].category == "Б"
    assert batch.evaluations[0].recommendation["edition_id"] == "1_1"


def test_source_failure_does_not_leak_exception(purchase_request, registry):
    class Broken:
        def candidates(self, *_):
            raise httpx.ConnectError("synthetic-private-error")

    batch = ML2Pipeline(registry, sources=Broken()).run(
        purchase_request, live_sources=True
    )
    assert batch.evaluations[0].classification.status == "SOURCE_ERROR"
    assert "synthetic-private-error" not in json.dumps(batch.details())


def test_provider_builds_strict_json_without_editing_colleague_client(monkeypatch):
    from llm_rag.ml2.provider import ML2GigaChatProvider

    calls = []

    class FakeSDK:
        def __init__(self, **kwargs):
            pass

        def chat(self, payload):
            calls.append(payload)
            return SimpleNamespace(
                usage=SimpleNamespace(total_tokens=42),
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            role="assistant", content=json.dumps(complete(None, None))
                        ),
                    )
                ],
            )

        def close(self):
            pass

    monkeypatch.setattr("llm_rag.providers.gigachat.GigaChat", FakeSDK)
    purchase_request = ClassificationRequest(
        "r", "i", "J45.9", "Тестовое вещество", (fragment(),), {}
    )
    with ML2GigaChatProvider(credentials="synthetic", enabled=True) as provider:
        result = classify_pair(purchase_request, gigachat_completer(provider))
        assert result.proposed_category == "Б"
        assert provider.last_total_tokens == 42
    assert len(calls) == 1
    assert calls[0].additional_fields["response_format"]["strict"] is True


def test_parser_extracts_treatment_and_source_metadata():
    html = (
        """<h1>Синтетическая КР</h1><dl class="krt-pass">
    <dt>ID:</dt><dd>1_1</dd><dt>Кодирование по МКБ-10:</dt><dd>J45</dd>
    <dt>Возрастная категория:</dt><dd>Взрослые, дети</dd>
    <dt>Год утверждения:</dt><dd>2024</dd><dt>Статус:</dt><dd>Действует</dd>
    <dt>Статус применения:</dt><dd>Применяется</dd></dl>
    <p class="krt-srcl">Источник: Рубрикатор Минздрава России</p>
    <a href="https://cr.minzdrav.gov.ru/preview-cr/1_1">Оригинал</a>
    <h2 id="s3">3. Лечение</h2><ul id="r1" class="krt-r"><li>"""
        + QUOTE
        + """</li></ul>
    <h2 id="s4">4. Реабилитация</h2>"""
    )
    rec = parse_recommendation(html, BASE_URL + "/kr/test-1")
    assert rec.is_current_ministry_copy
    assert fragments_for_inn(rec, "тестовое вещество")[0].text == QUOTE


def test_new_package_has_no_imports_from_backend():
    import ast

    for path in (ROOT / "src/llm_rag/ml2").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [n.name for n in node.names]
                if isinstance(node, ast.Import)
                else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            )
            assert not any(
                n == "app" or n.startswith(("app.", "sqlalchemy", "fastapi"))
                for n in names
            )


def test_approved_rules_require_context_and_report_conflict(purchase_request, registry):
    data = {
        "corpus_version": "synthetic",
        "is_demo": True,
        "sources": [
            {
                "source_id": "s",
                "title": "Синтетическое правило",
                "source_type": "MASTER_LIST",
                "version": "1",
                "is_demo": True,
            }
        ],
        "fragments": [
            {
                "fragment_id": "rule-1",
                "source_id": "s",
                "location": "строка 1",
                "inn": "тестовое вещество",
                "diagnosis": purchase_request.diagnosis,
                "required_context": {"setting": "test"},
                "text": "Синтетическое правило А.",
                "assertions": [
                    {
                        "claim_id": "c1",
                        "kind": "CATEGORY",
                        "category": "А",
                        "dosage": None,
                        "dosage_status": None,
                        "upstream_reason": "Синтетическое правило А.",
                        "text": "Синтетическое правило А.",
                        "quote": "Синтетическое правило А.",
                    }
                ],
            }
        ],
    }
    corpus = Corpus.model_validate(data)
    batch = ML2Pipeline(registry, approved_corpus=corpus).run(purchase_request)
    assert batch.evaluations[0].classification.status == "NEEDS_CONTEXT"
    purchase_request.context["setting"] = "test"
    assert (
        ML2Pipeline(registry, approved_corpus=corpus)
        .run(purchase_request)
        .request.items[0]
        .category
        == "А"
    )
    other = json.loads(json.dumps(data["fragments"][0]))
    other["fragment_id"] = "rule-2"
    other["assertions"][0].update(claim_id="c2", category="Б")
    data["fragments"].append(other)
    conflict = ML2Pipeline(registry, approved_corpus=Corpus.model_validate(data)).run(
        purchase_request
    )
    assert conflict.evaluations[0].classification.status == "CONFLICT"
    assert conflict.request.items[0].category is None


def test_catalog_refresh_304_clears_stale_flag(tmp_path, monkeypatch):
    import base64
    import gzip
    from datetime import UTC, datetime, timedelta

    from llm_rag.ml2.clinicalrecs import recommendations_for_diagnosis

    data = {
        "RU": [["Синтетическая КР", "1_1", "2024", 3, 3, [], "J45", [], [], "", ""]]
    }
    packed = base64.b64encode(gzip.compress(json.dumps(data).encode())).decode()
    chunks = [
        packed[i * len(packed) // 11 : (i + 1) * len(packed) // 11] for i in range(11)
    ]
    html = '<script>const KRP = {"1":"test-1"}; const krPage = x => x;</script>'
    html += "".join(
        f'<script id="krdata{i}">{part}</script>' for i, part in enumerate(chunks)
    )
    (tmp_path / "catalog-ru.json").write_text(
        json.dumps(
            {
                "checked_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
                "html": html,
                "etag": "test",
            }
        ),
        encoding="utf-8",
    )

    class Client:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url):
            request = httpx.Request("GET", url)
            return (
                httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
                if url.endswith("robots.txt")
                else httpx.Response(304, request=request)
            )

    monkeypatch.setattr("llm_rag.ml2.clinicalrecs.httpx.Client", Client)
    candidates = recommendations_for_diagnosis("J45.9", cache_dir=tmp_path)
    assert len(candidates) == 1
    assert candidates[0].cache_state == "FRESH_CACHE"


def test_cli_uses_certificate_path_relative_to_selected_env(
    tmp_path, monkeypatch, purchase_request, registry
):
    from llm_rag.ml2.__main__ import main

    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "config.env").write_text(
        "GIGACHAT_CREDENTIALS=synthetic\nGIGACHAT_CA_BUNDLE_FILE=ca.crt\n",
        encoding="utf-8",
    )
    (settings / "ca.crt").write_text("synthetic placeholder", encoding="utf-8")
    request_path, registry_path = tmp_path / "request.json", tmp_path / "registry.json"
    request_path.write_text(purchase_request.model_dump_json(), encoding="utf-8")
    registry_path.write_text(registry.model_dump_json(), encoding="utf-8")
    captured = {}

    class Provider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            pass

    for key in (
        "GIGACHAT_CREDENTIALS",
        "GIGACHAT_ACCESS_TOKEN",
        "GIGACHAT_CA_BUNDLE_FILE",
        "GIGACHAT_CA_BUNDLE",
        "GIGACHAT_SCOPE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("llm_rag.ml2.provider.ML2GigaChatProvider", Provider)
    assert (
        main(
            [
                "--request",
                str(request_path),
                "--registry",
                str(registry_path),
                "--output",
                str(tmp_path / "output.json"),
                "--details",
                str(tmp_path / "details.json"),
                "--enable-api",
                "--env-file",
                str(settings / "config.env"),
            ]
        )
        == 0
    )
    assert captured["ca_bundle"] == str((settings / "ca.crt").resolve())
    assert captured["scope"] == "GIGACHAT_API_B2B"
    AnalysisRequest.model_validate_json((tmp_path / "output.json").read_bytes())
