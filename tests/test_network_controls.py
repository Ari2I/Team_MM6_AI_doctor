"""Положительный и отрицательный контроль; модель заменена явным тестовым ответом."""

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import prepare_network_case as prepare
from scripts import verify_network_acceptance as verify
from tests.test_network_acceptance import control_result

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.mark.parametrize("kind", ["positive", "negative", "shared-token"])
def test_public_library_controls_without_sdk(monkeypatch, tmp_path, kind):
    source = Path("ai/src").resolve()
    if not source.is_dir():
        pytest.skip("AI library is absent")
    monkeypatch.syspath_prepend(str(source))
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "gigachat", None)
    from llm_rag.ml2.classifier import ClassificationRequest, classify_pair
    from llm_rag.ml2.clinicalrecs import (
        ClinicalRecommendation,
        TreatmentSegment,
        fragments_for_inn,
    )

    quote = "Вещество альфа рекомендуется для лечения синтетического состояния."
    requested = "Отсутствующее-бета" if kind == "negative" else "Вещество альфа"
    if kind == "shared-token":
        quote = "Другое альфа рекомендуется для лечения синтетического состояния."
    recommendation = ClinicalRecommendation(
        page_url="https://example.org/synthetic",
        title="Синтетический диагноз",
        edition_id="1_1",
        year="2026",
        population="Взрослые",
        icd10=("Z00",),
        status="действует",
        application_status="применяется",
        source_label="Рубрикатор Минздрава",
        primary_url="https://example.org/synthetic",
        segments=(TreatmentSegment("Синтетический раздел", quote),),
        retrieved_at="2026-11-27T00:00:00Z",
    )
    fragments = fragments_for_inn(recommendation, requested)
    calls = []

    def complete(*args):
        calls.append(True)
        return dict(
            assessment="CORE_TREATMENT",
            conditions=[],
            missing_context=[],
            evidence=[dict(fragment_id=fragments[0].fragment_id, quote=quote)],
        )

    result = classify_pair(
        ClassificationRequest("r", "i", recommendation.title, requested, fragments, {}), complete
    )
    if kind == "positive":
        assert calls == [True]
        assert result.status.value == "PROPOSED" and result.proposed_category is not None
        assert [citation.quote for citation in result.citations] == [quote]
    elif kind == "negative":
        assert not calls and not fragments
        assert result.status.value == "NO_EVIDENCE"
        assert result.proposed_category is None and not result.citations
    else:
        assert calls == [True] and fragments
        assert result.status.value == "NEEDS_REVIEW" and result.proposed_category is None


def negative_result():
    result, _ = control_result()
    pair = result["pairs"][0]
    pair.update(
        citations=[],
        reasons=["NO_EVIDENCE"],
        decision=dict(
            classification_basis="NONE", upstream_rule_category=None, proposed_category=None
        ),
    )
    result["evidence_snapshot"]["documents"] = []
    result["human_report"] = dict(items=[dict(sources=[])])
    return result


def test_negative_acceptance_does_not_require_embeddings_or_citations():
    result = negative_result()
    result["retrieval_info"] = {}
    verify.assess_negative(result)


@pytest.mark.parametrize(
    "damage",
    ["category", "basis", "citation", "document", "report-source", "source-error", "fallback"],
)
def test_negative_acceptance_rejects_invented_evidence_and_technical_failure(damage):
    result = negative_result()
    pair = result["pairs"][0]
    if damage == "category":
        pair["decision"]["proposed_category"] = "А"
    elif damage == "basis":
        pair["decision"]["classification_basis"] = "APPROVED_RULE"
    elif damage == "citation":
        pair["citations"] = [dict(quote="Синтетическая посторонняя цитата.")]
    elif damage == "document":
        result["evidence_snapshot"]["documents"] = [dict(text="Синтетический фрагмент")]
    elif damage == "report-source":
        result["human_report"]["items"][0]["sources"] = [dict(title="Выдуманный источник")]
    elif damage == "source-error":
        pair["reasons"].append("SOURCE_ERROR")
    else:
        pair["generation_mode"] = "FALLBACK"
    with pytest.raises(ValueError):
        verify.assess_negative(result)


def test_negative_selection_rejects_noise_from_shared_word():
    entries = [
        SimpleNamespace(mnn="Вещество альфа", form="Форма", trade_name="Шум"),
        SimpleNamespace(mnn="Отсутствующее-бета", form="Форма", trade_name="Нет фрагментов"),
    ]
    text = "Другое альфа обсуждается в синтетическом источнике."
    recommendation = SimpleNamespace(segments=[SimpleNamespace(text=text)])
    index = SimpleNamespace(
        resolve=lambda name, form: next(e for e in entries if e.trade_name == name)
    )
    selected = prepare.select_control_entry(
        entries,
        recommendation=recommendation,
        index=index,
        load_fragments=lambda rec, inn, **kw: (
            [SimpleNamespace(text=text)] if inn == entries[0].mnn else []
        ),
        negative=True,
    )
    assert selected is entries[1]


def test_control_pair_is_published_atomically_with_shared_policy(tmp_path, monkeypatch):
    rec = SimpleNamespace(
        title="Синтетический диагноз", edition_id="1_1", primary_url="https://example.org/synthetic"
    )
    positive = SimpleNamespace(mnn="Вещество альфа", trade_name="Положительный", form="Форма 1")
    negative = SimpleNamespace(mnn="Отсутствующее-бета", trade_name="Отрицательный", form="Форма 2")
    folder = tmp_path / "controls"
    prepare.write_case(
        folder,
        code="Z00",
        group="adult",
        recommendation=rec,
        entry=positive,
        registry_version="synthetic-1",
        negative_entry=negative,
    )
    policy = json.loads((folder / "network-terms.json").read_text())
    assert policy["inns"] == [positive.mnn, negative.mnn]
    for directory, kind, entry in (
        (folder, "positive", positive),
        (folder / "negative", "negative", negative),
    ):
        case = json.loads((directory / "case.json").read_text())
        assert case["control_kind"] == kind and case["inn"] == entry.mnn
        assert (directory / "control.xlsx").is_file()
    original_write = prepare._write_case

    def broken(directory, **kwargs):
        if kwargs.get("control_kind") == "negative":
            raise ValueError("SYNTHETIC_ERROR")
        return original_write(directory, **kwargs)

    monkeypatch.setattr(prepare, "_write_case", broken)
    with pytest.raises(ValueError, match="SYNTHETIC_ERROR"):
        prepare.write_case(
            tmp_path / "broken",
            code="Z00",
            group="adult",
            recommendation=rec,
            entry=positive,
            registry_version="synthetic-1",
            negative_entry=negative,
        )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["controls"]


def test_new_baseline_preserves_old_control_and_index(tmp_path, monkeypatch):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    baseline = old / "embedding-before.json"
    baseline.write_text('["old"]')
    index = tmp_path / "index.json"
    index.write_text('{"vectors":{"synthetic-hash":[1]}}')
    monkeypatch.setattr(verify, "FOLDER", old)
    monkeypatch.setattr(verify, "INDEX", index)
    before = index.read_bytes()
    verify.baseline(new)
    assert baseline.read_text() == '["old"]'
    assert json.loads((new / "embedding-before.json").read_text()) == ["synthetic-hash"]
    assert index.read_bytes() == before
    with pytest.raises(ValueError, match="BASELINE_EXISTS"):
        verify.baseline(new)


def test_cli_forwards_separate_case_directory(monkeypatch, tmp_path):
    prepared = []
    baselines = []
    monkeypatch.setattr(logging, "disable", lambda _: None)
    monkeypatch.setattr(prepare, "prepare", lambda *args, **kw: prepared.append((args, kw)))
    monkeypatch.setattr(verify, "baseline", lambda folder: baselines.append(folder))
    assert (
        prepare.main(
            [
                "--icd10",
                "Z00",
                "--age-group",
                "adult",
                "--with-negative",
                "--case-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert prepared == [(("Z00", "adult"), dict(case_dir=tmp_path, with_negative=True))]
    assert verify.main(["--case-dir", str(tmp_path), "--baseline"]) == 0
    assert baselines == [tmp_path]
