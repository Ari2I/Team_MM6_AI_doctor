"""Проверка оснований по независимому снимку, без загрузки и разбора КР."""

from datetime import datetime

from pydantic import Field, model_validator

from llm_rag.contracts import Model, Text
from llm_rag.corpus import Corpus, normalize
from llm_rag.explanation_contracts import EvidenceCitation, EvidenceStatus, ExplanationInput, PairKey


class EvidenceDocument(Model):
    source_id: Text
    fragment_id: Text
    title: Text
    version: Text
    url: Text
    copy_url: Text | None = None
    location: Text
    text: Text
    diagnosis: Text
    inn: Text
    required_context: dict[Text, Text] = Field(default_factory=dict)
    retrieved_at: Text

    @model_validator(mode='after')
    def provenance(self):
        from urllib.parse import urlsplit
        for url in [self.url] + ([self.copy_url] if self.copy_url else []):
            parsed = urlsplit(url)
            if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError('Необходим публичный URL источника')
        if datetime.fromisoformat(self.retrieved_at.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('Время получения должно содержать часовой пояс')
        return self


class EvidenceSnapshot(Model):
    snapshot_version: Text
    documents: list[EvidenceDocument] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique_documents(self):
        ids = [(d.source_id, d.fragment_id, d.version) for d in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError('Повторный фрагмент снимка')
        return self


class ApprovedClaim(Model):
    claim_id: Text
    text: Text
    citation: EvidenceCitation


class VerifiedEvidence(PairKey):
    status: EvidenceStatus
    snapshot_version: Text
    approved_corpus_version: Text | None = None
    citations: list[EvidenceCitation] = Field(default_factory=list)
    approved_claims: list[ApprovedClaim] = Field(default_factory=list)
    reasons: list[Text] = Field(default_factory=list)
    missing_context: list[Text] = Field(default_factory=list)


def verify_ml2_evidence(input: ExplanationInput, snapshot: EvidenceSnapshot, *,
                        approved_corpus: Corpus | None = None) -> VerifiedEvidence:
    """Проверить цитаты; разрешать категорию только из отдельно размеченного корпуса."""
    citations, claims, reasons = [], [], []
    missing = set(input.missing_context)
    conflict = False
    docs = {(d.source_id, d.fragment_id, d.version): d for d in snapshot.documents}

    def conditions(required):
        # Известное несовпадение исключает правило, даже если другие условия неизвестны.
        if any(k in input.context and input.context[k] != v for k, v in required.items()):
            return False
        absent = {f'context.{k}' for k in required if k not in input.context}
        missing.update(absent)
        return not absent

    for condition in input.conditions:
        if normalize(condition) == normalize(input.diagnosis):
            continue
        # Свободное медицинское условие не интерпретируется по догадке.
        key, sep, value = condition.partition('=')
        if not sep or input.context.get(key) != value:
            missing.add('condition: ' + condition)
    for citation in input.citations:
        doc = docs.get((citation.source_id, citation.fragment_id, citation.version))
        if doc is None:
            missing.add('snapshot: ' + citation.fragment_id)
            reasons.append('Фрагмент или его редакция отсутствует в независимом снимке.')
            continue
        # ML-2 оставляет URL пустым для согласованного Corpus. В таком случае
        # разрешаем ссылку из снимка лишь после разрешения того же утверждения.
        resolved_rule = bool(approved_corpus and any(
            f.fragment_id == citation.fragment_id and f.source_id == citation.source_id
            and f.location == citation.location and normalize(f.diagnosis) == normalize(input.diagnosis)
            and input.inn and normalize(f.inn) == normalize(input.inn)
            and any(s.source_id == f.source_id and s.version == citation.version for s in approved_corpus.sources)
            and any(a.kind == 'CATEGORY' and a.quote == citation.quote
                    and a.upstream_reason == input.classification_reason for a in f.assertions)
            for f in approved_corpus.fragments))
        if not citation.url and resolved_rule:
            citation = citation.model_copy(update={'url': doc.url, 'copy_url': doc.copy_url})
        if (not input.inn or normalize(doc.inn) != normalize(input.inn)
                or normalize(doc.diagnosis) != normalize(input.diagnosis)
                or any(getattr(doc, k) != getattr(citation, k) for k in ('url', 'copy_url', 'location'))
                or citation.quote not in doc.text):
            reasons.append('Цитата, происхождение или область применения не совпадают со снимком.')
            continue
        applicable = conditions(doc.required_context)
        if any(k in input.context and input.context[k] != v for k, v in doc.required_context.items()):
            reasons.append('Условия источника не соответствуют контексту пары.')
            continue
        # Точное извлечение сохраняется и при неизвестной применимости.
        citations.append(citation.model_copy(update={'title': doc.title}))
        if not applicable:
            reasons.append('Применимость цитаты требует дополнительных сведений.')

    if approved_corpus is not None and input.inn:
        sources = {s.source_id: s for s in approved_corpus.sources}
        categories = set()
        for fragment in approved_corpus.fragments:
            if not any(a.kind == 'CATEGORY' for a in fragment.assertions):
                continue
            if normalize(fragment.inn) != normalize(input.inn) or normalize(fragment.diagnosis) != normalize(input.diagnosis):
                continue
            if not conditions(fragment.required_context):
                continue
            for assertion in fragment.assertions:
                if assertion.kind != 'CATEGORY':
                    continue
                categories.add(assertion.category)
                matched = next((c for c in citations if c.fragment_id == fragment.fragment_id
                    and c.source_id == fragment.source_id and c.version == sources[fragment.source_id].version
                    and c.location == fragment.location and c.quote == assertion.quote), None)
                if (matched and assertion.category == input.category
                        and assertion.upstream_reason == input.classification_reason):
                    claims.append(ApprovedClaim(claim_id=assertion.claim_id, text=assertion.text, citation=matched))
        conflict = len(categories) > 1 or bool(categories and input.category and input.category not in categories)
    if conflict:
        reasons.append('Применимые согласованные правила противоречат категории или друг другу.')
    status = ('SOURCE_CONFLICT' if conflict else 'INSUFFICIENT_DATA' if missing or not snapshot.documents
              else 'UNCONFIRMED' if reasons or not citations else 'VERIFIED')
    if status != 'VERIFIED':
        claims = []
    return VerifiedEvidence(request_id=input.request_id, item_id=input.item_id, diagnosis_id=input.diagnosis_id,
        status=status, snapshot_version=snapshot.snapshot_version,
        approved_corpus_version=approved_corpus.corpus_version if approved_corpus else None,
        citations=citations, approved_claims=claims, reasons=list(dict.fromkeys(reasons)), missing_context=sorted(missing))
