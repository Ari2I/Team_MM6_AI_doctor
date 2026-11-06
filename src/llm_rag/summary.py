"""Шаблонное резюме; денежные расчёты выполняются независимо от генерации."""

from decimal import Decimal, localcontext

from llm_rag.contracts import AnalysisRequest, CurrencyBreakdown, ItemResult, SummaryRef


def calculate_costs(request: AnalysisRequest) -> list[CurrencyBreakdown]:
    results = []
    for currency in sorted({item.original.currency for item in request.items}):
        items = [item for item in request.items if item.original.currency == currency]
        # Стандартная точность Decimal не должна приводить к незаметному округлению больших сумм.
        highest_place = max(item.original.amount.adjusted() for item in items)
        lowest_place = min(item.original.amount.as_tuple().exponent for item in items)
        precision = max(28, highest_place - lowest_place + 1 + len(str(len(items))) + 4)
        with localcontext() as context:
            context.prec = precision
            amounts = {key: Decimal("0") for key in ("А", "Б", "В", "UNCLASSIFIED")}
            for item in items:
                amounts[item.category or "UNCLASSIFIED"] += item.original.amount
            total = sum(amounts.values(), Decimal("0"))
            shares = {key: ((amount / total).quantize(Decimal("0.000001")) if total else None)
                      for key, amount in amounts.items()}
            results.append(CurrencyBreakdown(
                currency=currency, total_amount=total, amounts_by_upstream_category=amounts,
                shares_by_upstream_category=shares,
            ))
    return results


def build_summary(items: list[ItemResult]) -> tuple[str, list[SummaryRef], str, list[str]]:
    unresolved = [item.item_id for item in items if item.explanation_status != "SUPPORTED"]
    has_verified = any(item.claims for item in items)
    completeness = "COMPLETE" if not unresolved else ("PARTIAL" if has_verified else "UNRESOLVED")
    heading = {
        "COMPLETE": "Объяснения входных результатов подтверждены тестовым корпусом.",
        "PARTIAL": "Анализ неполный: часть результатов требует экспертного рассмотрения.",
        "UNRESOLVED": "Анализ не завершён: подтверждённые объяснения отсутствуют, требуется экспертное рассмотрение.",
    }[completeness]
    lines = [heading, "Демонстрационные данные; медицинская корректность для реальных пациентов не проверялась."]
    refs = []
    for item in items:
        lines.append(f"Позиция {item.item_id}: {item.explanation_status}.")
        for claim in item.claims:
            lines.append(claim.text)
            refs.append(SummaryRef(item_id=item.item_id, claim_id=claim.claim_id))
    if unresolved:
        lines.append("Нерешённые позиции: " + ", ".join(unresolved) + ".")
    lines.append("Затраты приведены по входной классификации №2, отдельно по валютам. Процент компенсации не определён.")
    return "\n".join(lines), refs, completeness, unresolved
