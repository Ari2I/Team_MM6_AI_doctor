"""Pure in-memory matching against a :class:`ReferenceSnapshot`."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from rapidfuzz import fuzz, process

from app.config import MatchingSettings, get_matching_settings
from app.schemas.analysis import (
    DiagnosisMatch,
    DrugMatch,
    ReferenceSnapshot,
)
from app.services.text_utils import normalize_text


def _best_scores(
    value: str, choices: Iterable[tuple[str, str]], threshold: float
) -> dict[str, float]:
    """Return the best score per entity id, searching over display labels."""

    labels = list(choices)
    if not labels:
        return {}
    results = process.extract(
        value,
        [label for label, _ in labels],
        scorer=fuzz.WRatio,
        score_cutoff=threshold,
    )
    scores: dict[str, float] = defaultdict(float)
    for _, score, index in results:
        entity_id = labels[index][1]
        scores[entity_id] = max(scores[entity_id], float(score))
    return dict(scores)


def _match_drug_with_settings(
    name: str, snapshot: ReferenceSnapshot, settings: MatchingSettings
) -> DrugMatch:
    value = normalize_text(name)
    if not value:
        return DrugMatch(confidence=0, status="not_found")

    exact = [
        drug
        for drug in snapshot.drugs
        if value in {drug.normalized_trade_name, drug.normalized_mnn}
    ]
    exact_mnns = sorted({drug.mnn for drug in exact})
    if len(exact_mnns) == 1:
        return DrugMatch(mnn=exact_mnns[0], confidence=1, status="matched")
    if len(exact_mnns) > 1:
        return DrugMatch(confidence=1, status="ambiguous", candidates=exact_mnns)

    labels = [(label, drug.mnn) for drug in snapshot.drugs for label in (drug.trade_name, drug.mnn)]
    scores = _best_scores(
        value,
        ((normalize_text(label), mnn) for label, mnn in labels),
        settings.drug_candidate_threshold,
    )
    if not scores:
        return DrugMatch(confidence=0, status="not_found")
    best = max(scores.values())
    candidates = sorted(
        mnn for mnn, score in scores.items() if score >= settings.drug_candidate_threshold
    )
    best_mnns = [mnn for mnn, score in scores.items() if score == best]
    confidence = best / 100
    if best >= settings.drug_auto_accept_threshold and len(best_mnns) == 1:
        return DrugMatch(mnn=best_mnns[0], confidence=confidence, status="matched")
    return DrugMatch(confidence=confidence, status="ambiguous", candidates=candidates)


def match_drug(
    name: str, snapshot: ReferenceSnapshot, settings: MatchingSettings | None = None
) -> DrugMatch:
    """Match a trade name or MNN, without I/O or logging of the input value."""

    return _match_drug_with_settings(name, snapshot, settings or get_matching_settings())


def _match_diagnosis_with_settings(
    name: str, snapshot: ReferenceSnapshot, settings: MatchingSettings
) -> DiagnosisMatch:
    value = normalize_text(name)
    if not value:
        return DiagnosisMatch(confidence=0, status="not_found")

    exact = [diagnosis for diagnosis in snapshot.diagnoses if diagnosis.normalized_name == value]
    exact_ids = sorted({diagnosis.diagnosis_id for diagnosis in exact})
    if len(exact_ids) == 1:
        return DiagnosisMatch(diagnosis_id=exact_ids[0], confidence=1, status="matched")
    if len(exact_ids) > 1:
        return DiagnosisMatch(confidence=1, status="ambiguous")

    choices = (
        (
            diagnosis.normalized_name or normalize_text(diagnosis.name),
            diagnosis.diagnosis_id,
        )
        for diagnosis in snapshot.diagnoses
    )
    scores = _best_scores(value, choices, settings.diagnosis_candidate_threshold)
    if not scores:
        return DiagnosisMatch(confidence=0, status="not_found")
    best = max(scores.values())
    best_ids = [diagnosis_id for diagnosis_id, score in scores.items() if score == best]
    confidence = best / 100
    if best >= settings.diagnosis_auto_accept_threshold and len(best_ids) == 1:
        return DiagnosisMatch(diagnosis_id=best_ids[0], confidence=confidence, status="matched")
    return DiagnosisMatch(confidence=confidence, status="ambiguous")


def match_diagnosis(
    name: str, snapshot: ReferenceSnapshot, settings: MatchingSettings | None = None
) -> DiagnosisMatch:
    """Match a diagnosis name using the same exact/fuzzy policy as drugs."""

    return _match_diagnosis_with_settings(name, snapshot, settings or get_matching_settings())
