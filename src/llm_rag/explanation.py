"""Детерминированные пояснения без повторной классификации и решения о выплате."""

from llm_rag.evidence_snapshot import VerifiedEvidence
from llm_rag.explanation_contracts import ExplanationInput, PairExplanation, pair_key
from llm_rag.providers.base import Provider


STATUS_REASONS = {
    'NEEDS_CONTEXT': 'ML-2 не хватает сведений для оценки применимости.',
    'NO_EVIDENCE': 'ML-2 не передал основание; это не доказывает отсутствие показаний.',
    'INN_UNRESOLVED': 'МНН не установлен; необходимо уточнить препарат.',
    'MULTI_INGREDIENT_REVIEW': 'Комбинированный состав требует ручной сверки.',
    'CONFLICT': 'ML-2 сообщил о конфликте оснований.',
    'NEEDS_REVIEW': 'Результат ML-2 требует дополнительной сверки.',
    'INVALID_RESPONSE': 'Ответ классификатора не прошёл проверку формата.',
    'SOURCE_ERROR': 'Источник ML-2 недоступен; применимость не установлена.',
    'PROVIDER_ERROR': 'Техническая ошибка провайдера ML-2.',
}


def explain_pair(input: ExplanationInput, evidence: VerifiedEvidence, *,
                 check_dosage: bool = False) -> PairExplanation:
    """Сохранить категорию и отдельно показать объём проверенного основания."""
    if pair_key(input) != pair_key(evidence):
        raise ValueError('Основания относятся к другой паре')
    status = evidence.status
    missing = sorted(set(input.missing_context + evidence.missing_context))
    reasons = list(evidence.reasons)
    if input.ml2_status in STATUS_REASONS:
        reasons.append(STATUS_REASONS[input.ml2_status])
    if input.ml2_status == 'CONFLICT' or status == 'SOURCE_CONFLICT':
        status = 'SOURCE_CONFLICT'
    elif input.ml2_status in {'INVALID_RESPONSE', 'SOURCE_ERROR', 'PROVIDER_ERROR'}:
        status = 'PROCESSING_ERROR'
    elif input.ml2_status in {'NEEDS_CONTEXT', 'INN_UNRESOLVED'} or missing or not input.inn:
        status = 'INSUFFICIENT_DATA'
        if not input.inn:
            missing = sorted(set(missing + ['inn']))
    elif input.ml2_status != 'PROPOSED':
        status = 'UNCONFIRMED'
    if check_dosage and input.dosage_check.status in {'INSUFFICIENT_DATA', 'NOT_CHECKED'}:
        missing = sorted(set(missing + input.dosage_check.missing_data + ['dosage_check']))
        if status not in {'SOURCE_CONFLICT', 'PROCESSING_ERROR'}:
            status = 'INSUFFICIENT_DATA'
    basis = ('APPROVED_RULE' if evidence.approved_claims and status == 'VERIFIED'
             else 'ML2_PROPOSAL' if input.category else 'NONE')
    if basis == 'APPROVED_RULE':
        text = f'Найдено применимое согласованное основание для исходной категории {input.category}.'
    elif input.category:
        text = f'ML-2 присвоил категорию {input.category} по действующим правилам классификации.'
        if status == 'VERIFIED':
            text += ' Цитаты и их происхождение проверены по текстовому снимку источника.'
    else:
        text = 'Категория ML-2 не установлена; требуется ручная сверка.'
    if reasons:
        text += ' ' + ' '.join(dict.fromkeys(reasons))
    if missing:
        text += ' Недостающие сведения: ' + ', '.join(missing) + '.'
    text += (' Дозировка не оценивалась в этом режиме.' if not check_dosage else
             ' Переданный статус дозировки: ' + input.dosage_check.status + '.')
    text += ' Проверка основания не заменяет медицинскую приёмку; решение принимает специалист.'
    return PairExplanation(request_id=input.request_id, item_id=input.item_id, diagnosis_id=input.diagnosis_id,
        category=input.category, ml2_status=input.ml2_status, classification_basis=basis,
        evidence_status=status, comment=text, citations=evidence.citations, reasons=list(dict.fromkeys(reasons)),
        missing_context=missing, dosage_check=input.dosage_check, snapshot_version=evidence.snapshot_version,
        approved_corpus_version=evidence.approved_corpus_version)


def render_explanation(input: ExplanationInput, evidence: VerifiedEvidence, *,
                       provider: Provider | None = None) -> PairExplanation:
    """Разрешить модели только точные извлечения; ограничения оформляет код."""
    from llm_rag.generation import INSTRUCTIONS
    from llm_rag.validation import GeneratedResponse

    result = explain_pair(input, evidence)
    if provider is None or not evidence.citations or input.ml2_status != 'PROPOSED':
        return result
    allowed = []
    for index, citation in enumerate(evidence.citations):
        claim = next((c for c in evidence.approved_claims if c.citation == citation), None)
        allowed.append(dict(claim_id=str(index), text=claim.text if claim and result.classification_basis == 'APPROVED_RULE'
                            else 'Материал для сверки.', fragment_id=citation.fragment_id, quote=citation.quote))
    payload = dict(schema_version='explanation.1', is_demo=True, instructions=INSTRUCTIONS,
                   corpus_version=evidence.snapshot_version,
                   item={'item_id': pair_key(input)}, evidence=allowed)
    try:
        # Копия отделяет пакет проверки от возможной мутации сторонним провайдером.
        import copy
        response = GeneratedResponse.model_validate(provider.generate(copy.deepcopy(payload)))
        if (response.item_id != pair_key(input) or len(response.claims) != len(allowed)
                or sorted((c.model_dump() for c in response.claims), key=lambda c: c['claim_id'])
                != sorted(allowed, key=lambda c: c['claim_id'])):
            raise ValueError('Неподтверждённый ответ модели')
        ordered_citations = [evidence.citations[int(c.claim_id)] for c in response.claims]
        ordered_claims = [claim for citation in ordered_citations for claim in evidence.approved_claims
                          if claim.citation == citation]
        result = explain_pair(input, evidence.model_copy(update={
            'citations': ordered_citations, 'approved_claims': ordered_claims}))
    except Exception:
        result.rendering_reasons = ['Оформление моделью не прошло проверку или недоступно; сохранён шаблон.']
    return result


def explain_pairs(inputs, snapshot, *, approved_corpus=None, provider: Provider | None = None) -> list[PairExplanation]:
    """Изолировать техническую ошибку одной пары от остальных результатов."""
    from llm_rag.evidence_snapshot import verify_ml2_evidence
    if len({pair_key(i) for i in inputs}) != len(inputs):
        raise ValueError('Повторный ключ пары')
    results = []
    for input in inputs:
        try:
            evidence = verify_ml2_evidence(input, snapshot, approved_corpus=approved_corpus)
            results.append(render_explanation(input, evidence, provider=provider))
        except Exception:
            evidence = VerifiedEvidence(request_id=input.request_id, item_id=input.item_id,
                diagnosis_id=input.diagnosis_id, status='PROCESSING_ERROR',
                snapshot_version=snapshot.snapshot_version,
                reasons=['Техническая ошибка обработки пары; требуется ручная сверка.'])
            result = explain_pair(input, evidence)
            result.evidence_status = 'PROCESSING_ERROR'
            result.classification_basis = 'ML2_PROPOSAL' if input.category else 'NONE'
            results.append(result)
    return results
