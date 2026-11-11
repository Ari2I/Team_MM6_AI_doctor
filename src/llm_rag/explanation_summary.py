"""Резюме для сверки и независимые денежные разрезы по уникальным покупкам."""

from decimal import Decimal
from typing import Literal

from pydantic import Field

from llm_rag.contracts import Model, Money, Text
from llm_rag.explanation_contracts import EvidenceCitation, PairExplanation, PurchaseAmount, pair_key


class ExplanationSummaryRef(Model):
    pair_id: Text
    citations: list[EvidenceCitation]


class ExplanationCosts(Model):
    currency: Text
    known_total_amount: Money
    unknown_amount_count: int = Field(ge=0)
    amounts_by_upstream_category: dict[str, Money]
    amount_requiring_review: Money
    amounts_by_diagnosis: dict[str, Money]


class ExplainedApplication(Model):
    schema_version: Literal['explanation.1'] = 'explanation.1'
    pairs: list[PairExplanation]
    summary_text: Text
    summary_refs: list[ExplanationSummaryRef]
    unresolved_pair_ids: list[Text]
    requires_review: Literal[True] = True
    snapshot_versions: list[Text]
    approved_corpus_versions: list[Text]
    costs_by_currency: list[ExplanationCosts]
    purchases_without_amount: list[list[Text]]


def summarize_explanations(pairs: list[PairExplanation], *,
                           purchases: list[PurchaseAmount]) -> ExplainedApplication:
    """amount уже относится ко всей покупке; количество повторно не учитывается."""
    pair_ids = [pair_key(p) for p in pairs]
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError('Повторный ключ пары')
    by_purchase = {}
    for p in pairs:
        by_purchase.setdefault((p.request_id, p.item_id), []).append(p)
    amounts = {}
    for purchase in purchases:
        key = purchase.request_id, purchase.item_id
        if key in amounts:
            raise ValueError('Повторная покупка в денежных данных')
        if key not in by_purchase:
            raise ValueError('Деньги относятся к неизвестной покупке')
        amounts[key] = purchase
    unresolved = [pair_key(p) for p in pairs
                  if p.evidence_status != 'VERIFIED' or p.classification_basis != 'APPROVED_RULE']
    costs, unknown = [], []
    for key in by_purchase:
        if key not in amounts or amounts[key].amount is None:
            unknown.append(list(key))
    for currency in sorted({p.currency for p in purchases}):
        total, review, count = Decimal('0'), Decimal('0'), 0
        categories, diagnoses = {}, {}
        for key, purchase in amounts.items():
            if purchase.currency != currency:
                continue
            if purchase.amount is None:
                count += 1
                continue
            amount = purchase.amount
            total += amount
            related = by_purchase[key]
            if any(p.requires_review for p in related):
                review += amount
            for category in {p.category or 'НЕ УСТАНОВЛЕНА' for p in related}:
                categories[category] = categories.get(category, Decimal('0')) + amount
            for diagnosis in {p.diagnosis_id for p in related}:
                diagnoses[diagnosis] = diagnoses.get(diagnosis, Decimal('0')) + amount
        costs.append(ExplanationCosts(currency=currency, known_total_amount=total,
            unknown_amount_count=count, amounts_by_upstream_category=categories,
            amount_requiring_review=review, amounts_by_diagnosis=diagnoses))
    lines = ['Резюме для секретаря. Суммы отражают расходы и исходные категории; размер компенсации не определяется.',
        'Разрезы категорий, диагнозов и ручной сверки могут пересекаться и не складываются между собой.',
        'Общие расходы учитывают каждую покупку один раз отдельно по валютам; неизвестные суммы не заменены нулём.']
    lines += [f'Пара {pair_key(p)}: {p.comment}' for p in pairs]
    return ExplainedApplication(pairs=pairs, summary_text='\n'.join(lines),
        summary_refs=[ExplanationSummaryRef(pair_id=pair_key(p), citations=p.citations) for p in pairs],
        unresolved_pair_ids=unresolved, snapshot_versions=sorted({p.snapshot_version for p in pairs}),
        approved_corpus_versions=sorted({p.approved_corpus_version for p in pairs if p.approved_corpus_version}),
        costs_by_currency=costs, purchases_without_amount=unknown)
