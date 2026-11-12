"""Регрессии независимой проверки ответа ML-2 без сети и секретов."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_rag.ml2.classifier import ClassificationRequest, EvidenceFragment, classify_pair


def classify(text, *, quote=None, inn="сальбутамол", assessment="CORE_TREATMENT", extra=()):
    fragment = EvidenceFragment("f", "s", "Синтетика", "1", "https://example.org", "1", text)
    request = ClassificationRequest("r", "i", "J45 Астма", inn, (fragment, *extra), {})
    return classify_pair(request, lambda *_: {
        "assessment": assessment, "conditions": [], "missing_context": [],
        "evidence": [{"fragment_id": "f", "quote": quote or text}],
    })


@pytest.mark.parametrize("assessment,quote", [
    ("CORE_TREATMENT", "Метформин рекомендуется для лечения заболевания."),
    ("SUPPORTIVE_TREATMENT", "Метформин применяется как вспомогательная терапия."),
])
def test_other_drug_sentence_cannot_support_requested_inn(assessment, quote):
    result = classify("Сальбутамол: раствор. " + quote, quote=quote, assessment=assessment)
    assert result.proposed_category is None


@pytest.mark.parametrize("text,assessment", [
    ("Рекомендуется избегать применения сальбутамола для лечения астмы.", "CORE_TREATMENT"),
    ("Рекомендуется отказаться от назначения сальбутамола при астме.", "CORE_TREATMENT"),
    ("Сальбутамол неэффективен для снятия симптомов.", "SUPPORTIVE_TREATMENT"),
])
def test_full_negative_recommendation_does_not_support_category(text, assessment):
    assert classify(text, assessment=assessment).proposed_category is None


def test_model_cannot_remove_negation_by_returning_substring():
    result = classify("Не рекомендуется сальбутамол для лечения астмы.",
                      quote="рекомендуется сальбутамол для лечения астмы.")
    assert result.status == "INVALID_RESPONSE"


@pytest.mark.parametrize("inn,group", [("сальбутамол", "КДБА"), ("будесонид", "ИГКС")])
def test_source_grounded_group_has_membership_citation(inn, group):
    quote = f"{group} рекомендуется для лечения астмы."
    membership = f"{group} ({inn}) входят в перечень лекарственных групп."
    extra = EvidenceFragment("map", "s", "Синтетика", "1", "https://example.org", "2", membership)
    result = classify(quote, inn=inn, extra=(extra,))
    assert result.proposed_category == "А"
    assert any(c.fragment_id == "map" and c.quote == membership for c in result.citations)


def test_group_membership_from_another_source_is_not_transferred():
    extra = EvidenceFragment("map", "other", "Синтетика", "1", "https://example.org", "2",
                             "КДБА (сальбутамол) входят в перечень лекарственных групп.")
    assert classify("КДБА рекомендуется для лечения астмы.", extra=(extra,)).proposed_category is None


def test_unknown_group_membership_is_not_invented():
    assert classify("КДБА рекомендуется для лечения астмы.").proposed_category is None


def test_pain_relief_group_does_not_match_preposition_inside_word():
    membership = "НПВП (ибупрофен) являются обезболивающими средствами."
    extra = EvidenceFragment("map", "s", "Синтетика", "1", "https://example.org", "2", membership)
    result = classify("НПВП применяют как вспомогательную терапию для снятия боли.",
                      inn="ибупрофен", assessment="SUPPORTIVE_TREATMENT", extra=(extra,))
    assert result.proposed_category == "Б"


@pytest.mark.parametrize("membership", [
    "КДБА (исключая сальбутамол) входят в перечень лекарственных групп.",
    "КДБА (сальбутамол противопоказан) входят в перечень лекарственных групп.",
    "КДБА (кроме сальбутамола) входят в перечень лекарственных групп.",
])
def test_group_membership_exceptions_are_not_positive_links(membership):
    extra = EvidenceFragment("map", "s", "Синтетика", "1", "https://example.org", "2", membership)
    assert classify("КДБА рекомендуется для лечения астмы.", extra=(extra,)).proposed_category is None


@pytest.mark.parametrize("text", [
    "Будесонид + формотерол рекомендуется для лечения астмы.",
    "Формотерол + будесонид рекомендуется для лечения астмы.",
    "Комбинация будесонида и формотерола рекомендуется для лечения астмы.",
])
def test_combination_recommendation_is_not_transferred_to_component(text):
    assert classify(text, inn="будесонид").proposed_category is None


def test_independent_component_recommendation_survives_other_combination_clause():
    text = "Будесонид рекомендуется для лечения астмы; ипратропий + фенотерол обсуждаются отдельно."
    assert classify(text, inn="будесонид").proposed_category == "А"


def test_classify_purchase_compatibility_wrapper_resolves_registry_and_preserves_citations():
    from llm_rag.ml2.classifier import classify_purchase
    from llm_rag.ml2.registry import RegistrySnapshot

    root = Path(__file__).resolve().parents[2]
    registry = RegistrySnapshot.load(root / "examples/ml2/registry.synthetic.json")
    text = "Демо-вещество-а рекомендуется для лечения заболевания."
    fragment = EvidenceFragment("f", "s", "Синтетика", "1", "https://example.org", "1", text)
    result = classify_purchase(registry, request_id="r", item_id="i", trade_name="Демо-А", dosage=None,
                               diagnosis="Синтетический диагноз", context={}, evidence_fragments=(fragment,),
                               complete_json=lambda *_: {"assessment": "CORE_TREATMENT", "conditions": [],
                                                         "missing_context": [], "evidence": [{"fragment_id": "f", "quote": text}]})
    assert result.proposed_category == "А"
    assert result.inn == "демо-вещество-а"
    assert result.registry_citations


def test_group_and_drug_are_linked_by_atc_codes_in_same_source():
    quote = "Рекомендуется назначение КДБА (код АТХ R03AC) для лечения астмы."
    membership = "Сальбутамол (код АТХ R03АС02) выпускается в ингаляторе."
    extra = EvidenceFragment("map", "s", "Синтетика", "1", "https://example.org", "2", membership)
    result = classify(quote, extra=(extra,))
    assert result.proposed_category == "А"
    assert any(c.quote == membership for c in result.citations)


def test_negative_atc_membership_does_not_restore_positive_category():
    membership = "Не применяют сальбутамол (код АТХ R03AC02) при астме."
    extra = EvidenceFragment("map", "s", "Синтетика", "1", "https://example.org", "2", membership)
    assert classify("Рекомендуется назначение КДБА (код АТХ R03AC) для лечения астмы.",
                    extra=(extra,)).proposed_category is None


def test_oversized_external_fragment_is_rejected_before_model_call():
    from llm_rag.ml2.classifier import prompt_for
    fragment = EvidenceFragment("f", "s", "Синтетика", "1", "https://example.org", "1", "x" * 100001)
    request = ClassificationRequest("r", "i", "J45", "сальбутамол", (fragment,), {})
    with pytest.raises(ValueError):
        prompt_for(request)


@pytest.mark.parametrize("field,value", [("conditions", ["x"] * 21),
                                         ("missing_context", ["x" * 201])])
def test_local_response_validation_enforces_array_limits(field, value):
    text = "Сальбутамол рекомендуется для лечения астмы."
    fragment = EvidenceFragment("f", "s", "Синтетика", "1", "https://example.org", "1", text)
    request = ClassificationRequest("r", "i", "J45", "сальбутамол", (fragment,), {})
    response = {"assessment": "CORE_TREATMENT", "conditions": [], "missing_context": [],
                "evidence": [{"fragment_id": "f", "quote": text}], field: value}
    assert classify_pair(request, lambda *_: response).status == "INVALID_RESPONSE"


def test_provider_validates_schema_locally_even_if_sdk_accepts_invalid_response(monkeypatch):
    from llm_rag.ml2.provider import ML2GigaChatProvider
    from llm_rag.providers.base import ProviderError

    class SDK:
        def __init__(self, **_):
            pass

        def chat(self, _):
            return SimpleNamespace(usage=None, choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(role="assistant", content='{"value":"bad"}'))])

        def close(self):
            pass

    monkeypatch.setattr("llm_rag.providers.gigachat.GigaChat", SDK)
    with ML2GigaChatProvider(credentials="synthetic", enabled=True) as provider:
        with pytest.raises(ProviderError):
            provider.complete_json({}, "Синтетика", {
                "type": "object", "properties": {"value": {"enum": ["allowed"]}},
                "required": ["value"], "additionalProperties": False,
            })


def test_fragments_json_array_returns_controlled_cli_error(tmp_path):
    from llm_rag.ml2.__main__ import main

    root = Path(__file__).resolve().parents[2]
    fragments = tmp_path / "fragments.json"
    fragments.write_text(json.dumps([]), encoding="utf-8")
    assert main(["--request", str(root / "examples/request.json"), "--registry",
                 str(root / "examples/ml2/registry.synthetic.json"), "--output", str(tmp_path / "out.json"),
                 "--fragments", str(fragments)]) == 2
    assert not (tmp_path / "out.json").exists()


@pytest.mark.parametrize("field", ["text", "source_id"])
def test_fragments_with_nonstring_fields_return_controlled_cli_error(tmp_path, field):
    from llm_rag.ml2.__main__ import main

    root = Path(__file__).resolve().parents[2]
    row = {"fragment_id": "f", "source_id": "s", "title": "Синтетика", "version": "1",
           "url": "https://example.org", "location": "1", "text": "Синтетический текст."}
    row[field] = 123
    fragments = tmp_path / "fragments.json"
    fragments.write_text(json.dumps({"демо-вещество-а": [row]}), encoding="utf-8")
    assert main(["--request", str(root / "examples/request.json"), "--registry",
                 str(root / "examples/ml2/registry.synthetic.json"), "--output", str(tmp_path / "out.json"),
                 "--fragments", str(fragments)]) == 2
    assert not (tmp_path / "out.json").exists()
