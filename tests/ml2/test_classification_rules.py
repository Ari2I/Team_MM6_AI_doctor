"""Рабочие категории без Master List и сохранение проверяемой неопределённости."""

import json
from pathlib import Path

import pytest

from llm_rag.contracts import AnalysisRequest
from llm_rag.corpus import Corpus
from llm_rag.evidence_snapshot import EvidenceSnapshot
from llm_rag.explanation import explain_pairs
from llm_rag.explanation_contracts import prepare_explanation_inputs
from llm_rag.ml2 import classifier
from llm_rag.ml2.classifier import (
    ClassificationRequest,
    EvidenceFragment,
    classify_pair,
)
from llm_rag.ml2.pipeline import ML2Pipeline
from llm_rag.ml2.policy import ClassificationRules, load_classification_rules
from llm_rag.ml2.registry import RegistrySnapshot

ROOT = Path(__file__).resolve().parents[2]


def classify(assessment, text, *, missing=(), quote=None):
    request = ClassificationRequest(
        "synthetic-r",
        "synthetic-i",
        "Синтетический диагноз",
        "тестовое вещество",
        (
            EvidenceFragment(
                "f", "s", "Синтетика", "1", "https://example.org", "строка 1", text
            ),
        ),
        {},
    )
    return classify_pair(
        request,
        lambda *_: {
            "assessment": assessment,
            "conditions": [],
            "missing_context": list(missing),
            "evidence": []
            if assessment == "NOT_FOUND"
            else [{"fragment_id": "f", "quote": quote or text}],
        },
    )


@pytest.mark.parametrize(
    "assessment,text,expected",
    [
        (
            "CORE_TREATMENT",
            "Тестовое вещество рекомендуется для лечения заболевания.",
            "А",
        ),
        (
            "SUPPORTIVE_TREATMENT",
            "Тестовое вещество применяется как вспомогательная терапия.",
            "Б",
        ),
        ("OPTIONAL_PRODUCT", "Тестовое вещество является витамином.", "В"),
    ],
)
def test_category_without_approved_list(assessment, text, expected):
    result = classify(assessment, text)
    assert result.proposed_category == expected
    assert result.status == "PROPOSED"
    assert result.model_assessment == assessment
    assert result.policy_version == load_classification_rules().policy_version
    assert result.review_reason.startswith(f"Категория {expected}:")
    assert "предварительн" not in result.review_reason.casefold()
    assert load_classification_rules().mode == "CLINICAL_RECOMMENDATIONS"
    assert "подтверждённое правило" not in result.review_reason


@pytest.mark.parametrize(
    "assessment,text,missing,status",
    [
        (
            "CORE_TREATMENT",
            "Тестовое вещество не рекомендуется для лечения заболевания.",
            (),
            "NEEDS_REVIEW",
        ),
        (
            "SUPPORTIVE_TREATMENT",
            "Тестовое вещество противопоказано как симптоматическая терапия.",
            (),
            "NEEDS_REVIEW",
        ),
        ("CORE_TREATMENT", "Тестовое вещество: таблетки 10 мг.", (), "NEEDS_REVIEW"),
        (
            "CORE_TREATMENT",
            "Тестовое вещество рекомендуется после уточнения возраста.",
            ("возраст",),
            "NEEDS_CONTEXT",
        ),
        (
            "OPTIONAL_PRODUCT",
            "Тестовое вещество не является витамином.",
            (),
            "NEEDS_REVIEW",
        ),
        (
            "OPTIONAL_PRODUCT",
            "Тестовое вещество назначается при дефиците витаминов.",
            (),
            "NEEDS_REVIEW",
        ),
        ("OPTIONAL_PRODUCT", "Другой препарат является витамином.", (), "NEEDS_REVIEW"),
        (
            "EXPLICITLY_NOT_RECOMMENDED",
            "Тестовое вещество противопоказано при беременности.",
            (),
            "NEEDS_REVIEW",
        ),
        ("NOT_FOUND", "Нет упоминания нужного средства.", (), "NO_EVIDENCE"),
    ],
)
def test_uncertainty_and_negation_do_not_become_categories(
    assessment, text, missing, status
):
    result = classify(assessment, text, missing=missing)
    assert result.proposed_category is None
    assert result.status == status
    assert result.model_assessment == assessment


def test_invented_quote_is_still_rejected_for_core_treatment():
    result = classify(
        "CORE_TREATMENT", "Исходный текст.", quote="Выдуманное основное лечение."
    )
    assert result.status == "INVALID_RESPONSE"
    assert result.proposed_category is None


def test_mapping_is_read_from_rules_instead_of_hardcoded_letters(monkeypatch):
    rules = load_classification_rules()
    changed = dict(rules.assessments)
    changed["CORE_TREATMENT"] = changed["CORE_TREATMENT"].model_copy(
        update={"category": "Б"}
    )
    monkeypatch.setattr(
        classifier, "_RULES", rules.model_copy(update={"assessments": changed})
    )
    assert (
        classify(
            "CORE_TREATMENT", "Рекомендуется тестовое вещество для лечения заболевания."
        ).proposed_category
        == "Б"
    )


def test_invalid_rules_cannot_silently_change_category_semantics():
    rules = load_classification_rules().model_dump(mode="json")
    rules["assessments"]["CORE_TREATMENT"]["category"] = "A"
    with pytest.raises(ValueError):
        ClassificationRules.model_validate(rules)
    rules["assessments"]["CORE_TREATMENT"]["category"] = "А"
    rules["assessments"]["CORE_TREATMENT"]["quote_patterns"] = ["["]
    with pytest.raises(ValueError):
        ClassificationRules.model_validate(rules)


def test_category_reaches_existing_rag_and_approved_rule_has_priority():
    request = AnalysisRequest.model_validate_json(
        (ROOT / "examples/request.json").read_bytes()
    )
    request.items = request.items[:1]
    registry = RegistrySnapshot.load(ROOT / "examples/ml2/registry.synthetic.json")
    inn = request.items[0].inn
    text = f"{inn} рекомендуется как основное лечение заболевания."
    fragment = EvidenceFragment(
        "f", "s", "Синтетика", "1", "https://example.org", "строка 1", text
    )
    calls = []

    def model(*_):
        calls.append(True)
        return {
            "assessment": "CORE_TREATMENT",
            "conditions": [],
            "missing_context": [],
            "evidence": [{"fragment_id": "f", "quote": text}],
        }

    batch = ML2Pipeline(
        registry, complete_json=model, fragments_by_inn={inn: (fragment,)}
    ).run(request)
    assert batch.request.items[0].category == "А"
    assert not batch.evaluations[0].rule_applied
    assert batch.evaluations[0].classification.model_assessment == "CORE_TREATMENT"
    inputs = prepare_explanation_inputs(
        batch.request, diagnosis_id="d", ml2_details=batch.details()
    )
    pairs = explain_pairs(
        inputs, EvidenceSnapshot(snapshot_version="empty", documents=[])
    )
    assert pairs[0].category == "А"
    assert pairs[0].classification_basis == "ML2_PROPOSAL"
    json.dumps(batch.details(), ensure_ascii=False)
    corpus = Corpus.model_validate_json((ROOT / "examples/corpus.json").read_bytes())
    approved = ML2Pipeline(registry, approved_corpus=corpus, complete_json=model).run(
        request
    )
    assert approved.evaluations[0].rule_applied
    assert len(calls) == 1  # Применимое утверждённое правило не вызывает модель.
