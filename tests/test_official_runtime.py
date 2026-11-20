"""Публичный фасад получает каталог, редакцию и эмбеддинги без переключателей."""
import json

from llm_rag.integration import analyze_request, IntegrationOptions
from llm_rag.ml2.pipeline import ML2Pipeline
from tests.test_integration import request as multi_request
from tests.test_integration_model import FakeClient, model_request, resources
from tests.test_vectors import FixedEmbedder
from tests.ml2.test_ml2 import complete, QUOTE


class OfficialClient(FakeClient):
    def complete_json(self, payload, system, schema):
        self.calls += 1
        response = complete(None, None)
        response["evidence"][0]["fragment_id"] = payload["fragments"][0]["fragment_id"]
        return response


def test_facade_builds_resources_from_current_official_edition(monkeypatch, tmp_path):
    from llm_rag import runtime
    from llm_rag.ml2 import official

    calls = []
    client = OfficialClient()
    registry = resources(client).registry
    vector = FixedEmbedder()
    def public_json(operation, *, deadline, **params):
        calls.append((operation, params))
        if operation == "GetJsonClinrecs":
            return [dict(id="7_2", age=3, status=0, apply_status="Применяется", name="J45.9",
                         Mkb=["J45"], publish_date="2026-01-01")]
        assert params == {"id": "7_2"}
        return dict(id="7_2", adult=True, child=True, name="J45.9", status=0,
                    apply_status="Применяется", publish_date="2026-01-01", mkb="J45",
                    obj={"sections": [dict(title="3. Лечение", content=f"<p>Тестовое вещество. {QUOTE}</p>")]})

    monkeypatch.setattr(official, "_get", public_json)
    monkeypatch.setattr(runtime, "_registry", lambda *args: registry)
    monkeypatch.setattr(runtime, "source_root", lambda: None)
    monkeypatch.setattr(runtime, "default_data_dir", lambda: tmp_path)
    monkeypatch.delenv("RAG_NETWORK_TERMS", raising=False)
    monkeypatch.setattr(runtime, "ML2GigaChatProvider", lambda **_: client)
    monkeypatch.setattr(runtime, "corpus_embedder", lambda *args, **kwargs: vector)
    requested_codes = []
    candidates = official.ClinicalSources.candidates

    def trace_candidates(source, code, age):
        requested_codes.append(code)
        return candidates(source, code, age)

    monkeypatch.setattr(official.ClinicalSources, "candidates", trace_candidates)
    request = multi_request(3)
    request.diagnoses[0].name = "J45.9"
    request.diagnoses[1].icd10 = "A10"
    request.diagnoses[2].icd10 = "B20"
    request.diagnoses[0].icd10 = "J45.9"
    result = analyze_request(request, options=IntegrationOptions(max_pairs=1))
    assert requested_codes == ["J45.9"]
    assert [(p.diagnosis_id, p.reason) for p in result.unprocessed_pairs] == [
        ("D1", "BUDGET_EXHAUSTED"), ("D2", "BUDGET_EXHAUSTED")
    ]
    assert calls == [("GetJsonClinrecs", {}), ("GetClinrec", {"id": "7_2"})]
    assert vector.queries and result.retrieval_info.method == "vector"
    assert client.closed and result.pairs[0].generation_mode == "MODEL"
    assert result.pairs[0].citations[0].url.endswith("/7_2")
    assert result.pairs[0].citations[0].quote == QUOTE


def test_registry_failure_is_visible_to_backend(monkeypatch):
    def unavailable(cache, deadline):
        raise OSError("ГРЛС недоступен")

    monkeypatch.setattr("llm_rag.runtime._registry", unavailable)
    result = analyze_request(model_request())
    assert result.pairs[0].reasons == ["SOURCE_ERROR"]
    assert result.usage.calls == 0


def test_diagnosis_names_checked_for_each_code_and_edition(monkeypatch, tmp_path):
    from llm_rag import runtime
    from llm_rag.ml2 import official

    registry = resources(FakeClient()).registry
    catalog = [
        dict(id=edition, age=3, status=0, apply_status="Применяется", name=name,
             Mkb=codes, publish_date="2026-01-01")
        for edition, name, codes in [
            ("1_1", "Диагноз первый", ["A10", "A11"]),
            ("2_1", "Диагноз второй", ["B20", "A10"]),
        ]
    ]

    def public_json(operation, *, deadline, **params):
        if operation == "GetJsonClinrecs":
            return catalog
        row = next(row for row in catalog if row["id"] == params["id"])
        return dict(row, adult=True, child=True, mkb=",".join(row["Mkb"]),
                    obj={"sections": [dict(title="3. Лечение", content=f"<p>Тестовое вещество. {QUOTE}</p>")]})

    terms = tmp_path / "terms.json"
    terms.write_text(json.dumps(dict(diagnoses=["Диагноз второй"], inns=[], diagnosis_aliases=[
        dict(icd10="A10", edition="1_1", name="Первый синоним"),
    ])), encoding="utf-8")
    monkeypatch.setenv("RAG_NETWORK_TERMS", str(terms))
    monkeypatch.setattr(official, "_get", public_json)
    monkeypatch.setattr(runtime, "_registry", lambda *args: registry)
    monkeypatch.setattr(runtime, "source_root", lambda: None)
    monkeypatch.setattr(runtime, "default_data_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime, "corpus_embedder", lambda *args, **kwargs: FixedEmbedder())
    def provider(*, network_policy, **kwargs):
        client = OfficialClient()
        client.network_policy = network_policy
        return client

    monkeypatch.setattr(runtime, "ML2GigaChatProvider", provider)
    evaluations = []
    evaluate_pair = ML2Pipeline.evaluate_pair

    def record(pipeline, **kwargs):
        evaluations.append((kwargs["diagnosis"], kwargs["context"]["icd10"]))
        return evaluate_pair(pipeline, **kwargs)

    monkeypatch.setattr(ML2Pipeline, "evaluate_pair", record)
    rows = [
        ("Диагноз второй", "B20", "2_1"),
        ("Диагноз второй", "A10", "1_1"),
        ("ПЕРВЫЙ  синоним", "A10", "1_1"),
        ("Первый синоним", "A11", "1_1"),
        ("Первый синоним", "A10", "2_1"),
        ("Диагноз второй", "B20", "1_1"),
    ]
    value = multi_request(len(rows))
    for diagnosis, (name, code, edition) in zip(value.diagnoses, rows):
        diagnosis.name, diagnosis.icd10, diagnosis.recommendation_edition = name, code, edition
    # Неизвестное торговое название тоже не должно скрывать конфликт диагноза.
    value.purchases.append(value.purchases[0].model_copy(update={"item_id": "P2", "name": "Нет в реестре"}))
    value.links.append(value.links[1].model_copy(update={"item_id": "P2"}))
    result = analyze_request(value)
    assert evaluations == [(rows[i][0], rows[i][1]) for i in (0, 2)]
    assert len(result.pairs) == len(rows) + 1
    for i in (0, 2):
        assert result.pairs[i].decision.proposed_category == "Б"
        assert result.pairs[i].citations[0].url.endswith("/" + rows[i][2])
    assert result.pairs[5].reasons == ["NEEDS_CONTEXT"]
    for i in (1, 3, 4, 6):
        pair = result.pairs[i]
        assert pair.reasons == ["DIAGNOSIS_MISMATCH"]
        assert pair.decision.classification_basis == "NONE"
        assert not pair.citations
        assert "МКБ" in str(result.human_report["items"][i])
    assert {(d.diagnosis, d.required_context["icd10"]) for d in result.evidence_snapshot.documents} == {
        (rows[i][0], rows[i][1]) for i in (0, 2)
    }


def test_zero_pair_budget_skips_registry(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Загрузка реестра при нулевом лимите пар")

    monkeypatch.setattr("llm_rag.runtime._registry", unexpected)
    result = analyze_request(model_request(), options=IntegrationOptions(max_pairs=0))
    assert not result.pairs
    assert [p.reason for p in result.unprocessed_pairs] == ["BUDGET_EXHAUSTED"]
    assert result.usage.calls == 0
