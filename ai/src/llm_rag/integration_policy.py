"""Техническая политика: не объединять происхождение категорий и валюты."""

from decimal import Context, Decimal, localcontext
from llm_rag.contracts import AMOUNT_INTEGER_DIGITS, AMOUNT_SCALE
from llm_rag.integration_contracts import CategoryDecision, PurchaseTotals


def category_decision(upstream, proposal):
    """Сохранить оба решения; выбор при конфликте принадлежит владельцу политики."""
    basis = (
        "CONFLICT"
        if upstream and proposal and upstream != proposal
        else "APPROVED_RULE"
        if upstream
        else "ML2_PROPOSAL"
        if proposal
        else "NONE"
    )
    return CategoryDecision(
        upstream_rule_category=upstream, proposed_category=proposal, classification_basis=basis
    )


def purchase_totals(request):
    """Сумма за уникальную покупку, независимо от числа диагнозов и связей."""
    totals, known, unknown_amount, unknown_currency = {}, {}, [], []
    for purchase in request.purchases:
        if purchase.amount is None:
            unknown_amount.append(purchase.item_id)
        if purchase.currency is None:
            unknown_currency.append(purchase.item_id)
        if purchase.amount is not None and purchase.currency is not None:
            known.setdefault(purchase.currency, []).append(purchase.amount)
    for currency, amounts in known.items():
        precision = AMOUNT_INTEGER_DIGITS + AMOUNT_SCALE + len(str(len(amounts)))
        with localcontext(Context(prec=precision)):
            totals[currency] = sum(amounts, Decimal("0"))
    return PurchaseTotals(
        totals=totals, unknown_amount_item_ids=unknown_amount, unknown_currency_item_ids=unknown_currency
    )
