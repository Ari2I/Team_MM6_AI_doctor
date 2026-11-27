"""Synthetic reproductions of missing group evidence and early-window truncation."""

from dataclasses import replace
import socket

import pytest

from llm_rag.ml2.classifier import ClassificationRequest, classify_pair
from llm_rag.ml2.clinicalrecs import (
    CatalogCandidate,
    ClinicalRecommendation,
    TreatmentSegment,
    fragments_for_inn,
)
from llm_rag.ml2.official import ClinicalSources
from llm_rag.ml2.evidence_selection import EvidenceContextLimit


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = []

    def denied(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Network forbidden in synthetic retrieval regressions")

    class BlockedSocket(socket.socket):
        def __new__(cls, *args, **kwargs):
            return denied()

    monkeypatch.setattr(socket, "socket", BlockedSocket)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    yield
    assert not attempts, "A swallowed network attempt was detected"


def recommendation(segments):
    return ClinicalRecommendation(
        page_url="https://example.org/synthetic-copy",
        title="Синтетический диагноз",
        edition_id="359_3",
        year="2024",
        population="Взрослые",
        icd10=("J45",),
        status="действует",
        application_status="Применяется",
        source_label="Рубрикатор Минздрава",
        primary_url="https://cr.minzdrav.gov.ru/preview-cr/359_3",
        segments=tuple(TreatmentSegment(location, text) for location, text in segments),
        retrieved_at="2026-11-27T00:00:00Z",
    )


def test_group_recommendation_and_atc_membership_reach_classifier_together():
    membership = "Сальбутамол (код АТХ R03AC02) приведён в синтетическом перечне."
    role = "Рекомендуется назначение КДБА (код АТХ R03AC) для лечения заболевания."
    rec = recommendation([("3. Лечение, абз. 83", membership), ("3. Лечение, абз. 183", role)])
    fragments = fragments_for_inn(rec, "сальбутамол", max_fragments=8, window_chars=1600)
    assert any(role in fragment.text for fragment in fragments)
    assert any(membership in fragment.text for fragment in fragments)
    request = ClassificationRequest("r", "i", rec.title, "сальбутамол", fragments, {})
    target = next(fragment for fragment in fragments if role in fragment.text)
    result = classify_pair(
        request,
        lambda *_: {
            "assessment": "CORE_TREATMENT",
            "conditions": [],
            "missing_context": [],
            "evidence": [{"fragment_id": target.fragment_id, "quote": role}],
        },
    )
    assert result.proposed_category == "А"
    assert {citation.quote for citation in result.citations} == {role, membership}


def test_late_recommendation_survives_eight_early_mentions_and_keeps_age_condition():
    role = "Будесонид рекомендуется для лечения заболевания у пациентов старше 18 лет."
    rec = recommendation(
        [
            *[
                (f"3. Лечение, абз. {i}", f"Будесонид упомянут в синтетическом перечне {i}.")
                for i in range(1, 10)
            ],
            ("3. Лечение, абз. 20", role),
        ]
    )
    fragments = fragments_for_inn(rec, "будесонид", max_fragments=8, window_chars=1600)
    assert len(fragments) <= 8
    assert any(fragment.text == role for fragment in fragments)


@pytest.mark.parametrize("membership", ["missing", "other_edition", "other_inn"])
def test_group_without_proven_same_edition_membership_cannot_assign_category(membership):
    role = "Рекомендуется назначение КДБА (код АТХ R03AC) для лечения заболевания."
    rec = recommendation(
        [
            ("definition", "Сальбутамол (код АТХ R03AC02) приведён в синтетическом перечне."),
            ("role", role),
        ]
    )
    fragments = fragments_for_inn(rec, "сальбутамол")
    if membership == "missing":
        fragments = tuple(f for f in fragments if f.location != "definition")
    elif membership == "other_edition":
        fragments = tuple(
            replace(f, version="другая редакция") if f.location == "definition" else f for f in fragments
        )
    else:
        fragments = tuple(
            replace(f, text=f.text.replace("Сальбутамол", "Другое вещество"))
            if f.location == "definition"
            else f
            for f in fragments
        )
    target = next(f for f in fragments if f.location == "role")
    result = classify_pair(
        ClassificationRequest("r", "i", rec.title, "сальбутамол", fragments, {}),
        lambda *_: {
            "assessment": "CORE_TREATMENT",
            "conditions": [],
            "missing_context": [],
            "evidence": [{"fragment_id": target.fragment_id, "quote": role}],
        },
    )
    assert result.proposed_category is None


def test_late_negative_is_retained_with_positive_evidence():
    positive = "Будесонид рекомендуется для лечения заболевания."
    negative = "Будесонид не рекомендуется при синтетическом состоянии."
    rec = recommendation(
        [
            *[(f"3. Лечение, абз. {i}", f"Будесонид упомянут в перечне {i}.") for i in range(1, 10)],
            ("3. Лечение, абз. 20", positive),
            ("3. Лечение, абз. 30", negative),
        ]
    )
    fragments = fragments_for_inn(rec, "будесонид", max_fragments=8, window_chars=1600)
    assert any(fragment.text == positive for fragment in fragments)
    assert any(fragment.text == negative for fragment in fragments)


def test_independent_paragraph_order_does_not_change_selected_evidence():
    rec = recommendation(
        [
            *[(f"3. Лечение, абз. {i}", f"Будесонид упомянут в перечне {i}.") for i in range(1, 10)],
            ("3. Лечение, абз. 20", "Будесонид рекомендуется для лечения заболевания."),
        ]
    )
    first = fragments_for_inn(rec, "будесонид", max_fragments=8, window_chars=1600)
    second = fragments_for_inn(
        replace(rec, segments=tuple(reversed(rec.segments))), "будесонид", max_fragments=8, window_chars=1600
    )
    assert {(f.location, f.text) for f in first} == {(f.location, f.text) for f in second}


def test_search_normalizes_unicode_hyphen_without_changing_quote():
    original = "Тестовое\u2011вещество рекомендуется для лечения заболевания."
    rec = recommendation([("3. Лечение, абз. 1", original)])
    fragments = fragments_for_inn(rec, "тестовое-вещество", max_fragments=8, window_chars=1600)
    assert len(fragments) == 1 and fragments[0].text == original


def test_official_html_keeps_internal_line_breaks_and_punctuation():
    from llm_rag.ml2.official import _Paragraphs
    parser = _Paragraphs()
    parser.feed("<p>Тестовое\u2011вещество —<br>  рекомендуется, при условии.</p>")
    parser.flush()
    assert parser.paragraphs == ["Тестовое\u2011вещество —\n  рекомендуется, при условии."]


def test_official_reader_keeps_definitions_outside_treatment(monkeypatch):
    membership = "КДБА (сальбутамол) приведены в синтетическом перечне."
    role = "КДБА рекомендуется для лечения заболевания."
    data = dict(
        id="359_3",
        status=0,
        apply_status="Применяется",
        name="Синтетический диагноз",
        adult=True,
        child=False,
        publish_date="2024-01-01",
        mkb="J45",
        obj={
            "sections": [
                {"title": "1. Термины и определения", "content": f"<p>{membership}</p>"},
                {"title": "3. Лечение", "content": f"<p>{role}</p>"},
            ]
        },
    )
    monkeypatch.setattr("llm_rag.ml2.official._get", lambda *args, **kwargs: data)
    candidate = CatalogCandidate(
        title=data["name"],
        edition_id="359_3",
        published_at="2024-01-01",
        population="Взрослые",
        icd10=("J45",),
        page_url="https://cr.minzdrav.gov.ru/preview-cr/359_3",
        primary_url="https://cr.minzdrav.gov.ru/preview-cr/359_3",
    )
    rec = ClinicalSources().load(candidate).recommendation
    assert any(segment.text == membership and segment.locator.startswith("1.") for segment in rec.segments)
    fragments = fragments_for_inn(rec, "сальбутамол", max_fragments=8, window_chars=1600)
    assert any(role in fragment.text for fragment in fragments)


def test_oversized_group_restriction_cannot_be_hidden_by_short_mnn_mention():
    rec = recommendation(
        [
            ("definition", "КДБА (сальбутамол) приведены в синтетическом перечне."),
            ("restriction", "КДБА не рекомендуется при " + "а" * 4000 + "."),
        ]
    )
    with pytest.raises(EvidenceContextLimit):
        fragments_for_inn(rec, "сальбутамол")


def test_short_group_restriction_keeps_explicit_membership_with_positive():
    restriction = "КДБА не рекомендуется при синтетическом состоянии."
    membership = "КДБА (сальбутамол) приведены в синтетическом перечне."
    rec = recommendation(
        [
            ("definition", membership),
            ("positive", "Сальбутамол рекомендуется для лечения заболевания."),
            ("restriction", restriction),
        ]
    )
    fragments = fragments_for_inn(rec, "сальбутамол", max_fragments=3)
    assert {f.text for f in fragments} == {
        membership,
        restriction,
        "Сальбутамол рекомендуется для лечения заболевания.",
    }
