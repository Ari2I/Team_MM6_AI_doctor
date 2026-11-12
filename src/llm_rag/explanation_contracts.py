"""Контракт пояснений пары, независимый от прежнего конвейера v1."""

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from llm_rag.contracts import AnalysisRequest, Category, DemoFlag, DosageCheck, Model, Money, Text


class PairKey(Model):
    request_id: Text
    item_id: Text
    diagnosis_id: Text


class EvidenceCitation(Model):
    source_id: Text
    fragment_id: Text
    title: str = ''
    version: str
    url: str
    copy_url: str | None = None
    location: str
    quote: Text


ML2Status = Literal['PROPOSED', 'NEEDS_CONTEXT', 'NO_EVIDENCE', 'INN_UNRESOLVED',
                    'MULTI_INGREDIENT_REVIEW', 'CONFLICT', 'NEEDS_REVIEW',
                    'INVALID_RESPONSE', 'SOURCE_ERROR', 'PROVIDER_ERROR']
EvidenceStatus = Literal['VERIFIED', 'INSUFFICIENT_DATA', 'UNCONFIRMED',
                         'SOURCE_CONFLICT', 'PROCESSING_ERROR']


class ExplanationInput(PairKey):
    schema_version: Literal['explanation.1'] = 'explanation.1'
    is_demo: DemoFlag = True
    diagnosis: Text
    inn: Text | None
    category: Category | None
    ml2_status: ML2Status
    rule_applied: bool = False
    classification_reason: str | None = None
    conditions: list[Text] = Field(default_factory=list)
    missing_context: list[Text] = Field(default_factory=list)
    citations: list[EvidenceCitation] = Field(default_factory=list)
    source_refs: list[Text] = Field(default_factory=list)
    context: dict[Text, Text] = Field(default_factory=dict)
    dosage_check: DosageCheck


def pair_key(value: PairKey) -> str:
    """Однозначный ключ без коллизий при разделителях в исходных ID."""
    return json.dumps([value.request_id, value.item_id, value.diagnosis_id], ensure_ascii=False)


def missing_context_label(value: str) -> str:
    """Назвать служебные ограничения понятным языком, сохранив машинные ключи отдельно."""
    return {
        'clinical_applicability': 'применимость клинических условий независимо не проверена',
        'citation_age_applicability': 'возрастные условия цитаты требуют отдельной сверки',
    }.get(value, value)


class PairExplanation(PairKey):
    schema_version: Literal['explanation.1'] = 'explanation.1'
    category: Category | None
    ml2_status: ML2Status
    classification_basis: Literal['APPROVED_RULE', 'ML2_PROPOSAL', 'NONE']
    evidence_status: EvidenceStatus
    comment: Text
    citations: list[EvidenceCitation]
    reasons: list[Text]
    missing_context: list[Text]
    requires_review: Literal[True] = True
    dosage_check: DosageCheck
    snapshot_version: Text
    approved_corpus_version: Text | None = None
    rendering_reasons: list[Text] = Field(default_factory=list)


class PurchaseAmount(Model):
    request_id: Text
    item_id: Text
    amount: Money | None
    currency: str = Field(pattern=r'^[A-Z]{3}$')


def prepare_explanation_inputs(request: AnalysisRequest, *, diagnosis_id: str,
                               ml2_details: Mapping[str, object]) -> list[ExplanationInput]:
    """Связать полный ответ ML-2 по ID, без предположений о порядке строк."""
    if ml2_details.get('request_id', request.request_id) != request.request_id:
        raise ValueError('Не совпадает request_id ML-2')
    if ml2_details.get('schema_version', 'ml2.1') != 'ml2.1':
        raise ValueError('Неизвестная версия ML-2')
    rows = ml2_details.get('items', [])
    if not isinstance(rows, list):
        raise ValueError('items должен быть списком')
    known = {i.item_id for i in request.items}
    by_id = {}
    for row in rows:
        c = row['classification']
        item_id = c['item_id']
        if item_id not in known or item_id in by_id:
            raise ValueError('Неизвестный или повторный item_id ML-2')
        if c.get('request_id', request.request_id) != request.request_id or c.get('diagnosis', request.diagnosis) != request.diagnosis:
            raise ValueError('Не совпадает заявка или диагноз ML-2')
        by_id[item_id] = row
    result = []
    for item in request.items:
        row = by_id.get(item.item_id)
        c = row['classification'] if row else {}
        if row and (c.get('inn') != item.inn or c.get('proposed_category') != item.category):
            raise ValueError('Не совпадает МНН или категория ML-2')
        missing = list(c.get('missing_context', []))
        missing += ['ml2_details.' + k for k in ('schema_version', 'request_id') if k not in ml2_details]
        if row:
            missing += ['classification.' + k for k in ('request_id', 'diagnosis', 'status') if k not in c]
        if not row:
            missing.append('ml2_details')
        result.append(ExplanationInput(
            request_id=request.request_id, item_id=item.item_id, diagnosis_id=diagnosis_id,
            diagnosis=request.diagnosis, inn=item.inn, category=item.category,
            ml2_status=str(c.get('status', 'NEEDS_CONTEXT')),
            rule_applied=row.get('rule_applied', False) if row else False,
            classification_reason=c.get('review_reason', item.classification_reason),
            conditions=list(c.get('conditions', [])), missing_context=missing,
            citations=[EvidenceCitation.model_validate(dict(v)) for v in c.get('citations', [])],
            source_refs=item.source_refs, context=request.context, dosage_check=item.dosage_check,
        ))
    return result
