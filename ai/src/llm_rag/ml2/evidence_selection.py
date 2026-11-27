"""Collect and pack complete evidence bundles before applying model limits.

Selection is not classification: only the existing classifier may assign a
category. Group bundles reuse its source/edition checks and keep every link.
"""

from dataclasses import dataclass, replace
from hashlib import sha256
import re

from .classifier import (
    Citation,
    ClassificationRequest,
    EvidenceFragment,
    _group_support,
    _quote_options,
    _role_supported,
    prompt_for,
)
from .policy import load_classification_rules
from .registry import inn_key, name_key
from .clinicalrecs import ClinicalSourceError


class EvidenceContextLimit(ClinicalSourceError):
    pass


@dataclass(frozen=True)
class EvidenceBundle:
    fragments: tuple[EvidenceFragment, ...]
    priority: int

    @property
    def identity(self):
        return tuple(sorted((f.source_id, f.version, f.fragment_id) for f in self.fragments))


def fragment_key(fragment):
    return fragment.source_id, fragment.version, fragment.fragment_id


def _inn_pattern(inn):
    ingredient = re.escape(inn_key(inn)).replace(r"\ ", r"\s+")
    return re.compile(r"(?<!\w)" + ingredient + r"[а-я]{0,4}(?!\w)", re.IGNORECASE)


def _mention_patterns(inn):
    # Preserve broad retrieval candidates for validation/negative controls.
    # A shared token never establishes the identity required for a category.
    return tuple(
        re.compile(r"(?<!\w)" + re.escape(t) + r"[а-я]{0,4}(?!\w)", re.I)
        for t in re.findall(r"[a-zа-я0-9-]+", inn_key(inn))
        if len(t) >= 4
    )


def evidence_bundles(fragments, inn, diagnosis="Selection", context=None, checkpoint=None):
    """Discover direct/linked roles and retain negative/uncertain mentions.

    All candidate fragments must originate in one selected recommendation. The
    classifier's group helper additionally checks source, version and URL.
    """
    rules = load_classification_rules()
    fragments = tuple(fragments)
    by_identity = {fragment_key(f): f for f in fragments}
    request = ClassificationRequest("selection", "pair", diagnosis, inn, fragments, context or {})
    pattern = _inn_pattern(inn)
    mention_patterns = _mention_patterns(inn)
    bundles = {}
    for fragment in fragments:
        if checkpoint:
            checkpoint()
        mentions_inn = bool(pattern.search(name_key(fragment.text)))
        priority = 2
        if mentions_inn and any(
            re.search(p, name_key(fragment.text), re.IGNORECASE)
            for rule in rules.assessments.values()
            for p in rule.reject_patterns
        ):
            priority = 0
        if mentions_inn:
            candidate = EvidenceBundle((fragment,), priority)
            bundles[candidate.identity] = candidate
        elif any(p.search(name_key(fragment.text)) for p in mention_patterns):
            candidate = EvidenceBundle((fragment,), 3)
            bundles[candidate.identity] = candidate
        for quote in _quote_options(fragment.text):
            citation = Citation(
                fragment.fragment_id,
                fragment.source_id,
                fragment.title,
                fragment.version,
                fragment.url,
                fragment.location,
                quote,
                fragment.copy_url,
            )
            for rule in rules.assessments.values():
                if not rule.category:
                    continue
                direct = _role_supported(inn, quote, rule)
                linked = () if direct else _group_support(request, citation, rule)
                if not direct and not linked:
                    continue
                members = {fragment_key(fragment): fragment}
                for link in linked:
                    identity = link.source_id, link.version, link.fragment_id
                    if identity not in by_identity:
                        raise EvidenceContextLimit("GROUP_LINK_OUTSIDE_CANDIDATES")
                    members[identity] = by_identity[identity]
                bundle = EvidenceBundle(tuple(members[k] for k in sorted(members)), 1)
                existing = bundles.get(bundle.identity)
                if existing is None or existing.priority > bundle.priority:
                    bundles[bundle.identity] = bundle
    return tuple(sorted(bundles.values(), key=lambda b: (b.priority, b.identity)))


def candidate_fragments(recommendation, inn, *, window_chars=900, checkpoint=None):
    """Scan every paragraph and preserve original characters and sentence bounds."""
    if not recommendation.is_current_ministry_copy:
        return ()
    if not 200 <= window_chars <= 3000:
        raise ValueError("Invalid evidence window size")
    source_id = sha256(f"{recommendation.primary_url}\0{recommendation.version}".encode()).hexdigest()[:24]
    candidates = {}
    seen_text = {}
    for segment in recommendation.segments:
        if checkpoint:
            checkpoint()
        sentences = [m.span() for m in re.finditer(r"\S.*?(?:[.!?](?=\s|$)|$)", segment.text, re.DOTALL)]
        for index, (start, end) in enumerate(sentences):
            sentence = segment.text[start:end]
            if end - start > window_chars:
                # Long relevant evidence must not be hidden by a quota or cut.
                if _inn_pattern(inn).search(name_key(sentence)) or any(
                    p.search(name_key(sentence)) for p in _mention_patterns(inn)
                ):
                    raise EvidenceContextLimit("Релевантное предложение превышает предел контекста.")
                continue
            left = right = index
            while left > 0 and end - sentences[left - 1][0] <= window_chars:
                left -= 1
                start = sentences[left][0]
            while right + 1 < len(sentences) and sentences[right + 1][1] - start <= window_chars:
                right += 1
                end = sentences[right][1]
            text = segment.text[start:end].strip()
            identity = sha256(
                f"{recommendation.primary_url}\0{segment.locator}\0{start}\0{text}".encode()
            ).hexdigest()[:24]
            fragment = EvidenceFragment(
                identity,
                source_id,
                recommendation.title,
                recommendation.version,
                recommendation.primary_url,
                segment.locator,
                text,
                recommendation.page_url,
            )
            # Exact repeated paragraphs can occur in tables/headers; choose a
            # stable locator, never normalize the evidence that will be quoted.
            prior = seen_text.get(text)
            if prior is None or (fragment.location, identity) < (prior.location, prior.fragment_id):
                if prior is not None:
                    candidates.pop(prior.fragment_id, None)
                seen_text[text] = fragment
                candidates[identity] = fragment
    bundles = evidence_bundles(tuple(candidates.values()), inn, recommendation.title, checkpoint=checkpoint)
    selected = {fragment_key(f): f for bundle in bundles for f in bundle.fragments}
    return tuple(selected[k] for k in sorted(selected))


def rank_bundles(bundles, similarities=None):
    similarities = similarities or {}
    return tuple(
        sorted(
            bundles,
            key=lambda b: (
                b.priority,
                -max((similarities.get(fragment_key(f), 0.0) for f in b.fragments), default=0),
                b.identity,
            ),
        )
    )


def pack_evidence(bundles, *, inn, diagnosis, context, max_context_bytes, max_fragments=20):
    """Pack all bundles. A bundle is never split or silently discarded."""
    packets = []
    active = {}
    covered = set()
    active_priority = None

    def fits(members):
        if len(members) > max_fragments:
            return False
        try:
            # Public pipeline preserves identical IDs across editions with aliases.
            values = tuple(members.values())
            if len({f.fragment_id for f in values}) != len(values):
                values = tuple(replace(f, fragment_id=f"source-fragment-{i}") for i, f in enumerate(values))
            _, payload = prompt_for(
                ClassificationRequest(
                    "selection",
                    "pair",
                    diagnosis,
                    inn,
                    values,
                    context,
                )
            )
            return len(payload.encode()) <= max_context_bytes
        except ValueError:
            return False

    for bundle in bundles:
        addition = {fragment_key(f): f for f in bundle.fragments}
        if set(addition) <= covered:
            continue
        merged = active | addition
        if active and (not fits(merged) or (active_priority == 0) != (bundle.priority == 0)):
            packets.append(tuple(active.values()))
            covered.update(active)
            active = {}
            merged = addition
        if not fits(merged):
            raise EvidenceContextLimit("EVIDENCE_BUNDLE_EXCEEDS_CONTEXT")
        active = merged
        active_priority = bundle.priority
    if active:
        packets.append(tuple(active.values()))
    return tuple(packets)
