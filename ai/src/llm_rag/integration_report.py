"""Детерминированное публичное представление результата фасада."""

from llm_rag.human_report import readable_text, STATUS_LABELS


def build_integration_human_report(request, pairs, pending, costs):
    """Сохранить структуру human-report.1; одна строка соответствует явной паре."""
    purchases = {p.item_id: p for p in request.purchases}
    diagnoses = {d.diagnosis_id: d for d in request.diagnoses}
    rows = []
    for number, pair in enumerate(pairs, 1):
        category = pair.decision.upstream_rule_category or pair.decision.proposed_category
        if pair.decision.classification_basis == "CONFLICT":
            category = f"конфликт: по правилу {pair.decision.upstream_rule_category}; предложение модели {pair.decision.proposed_category}"
        elif pair.decision.upstream_rule_category:
            category = category + " (по внешнему правилу)"
        elif pair.decision.proposed_category:
            category = category + " (предварительное предложение модели)"
        rows.append(
            dict(
                number=number,
                name=purchases[pair.item_id].name,
                diagnosis=diagnoses[pair.diagnosis_id].name,
                category=category or "не установлена",
                evidence=STATUS_LABELS[pair.evidence_status],
                comment=readable_text(pair.explanation.comment)
                if pair.explanation
                else "Проверку не удалось завершить; требуется ручная сверка.",
                established=[],
                uncertain=[],
                questions=[],
                actions=["Передайте результат специалисту для сверки."],
                sources=[
                    dict(
                        number=i,
                        title=c.title,
                        version=c.version,
                        url=c.url,
                        copy_url=c.copy_url,
                        location=c.location,
                        quote=c.quote,
                    )
                    for i, c in enumerate(pair.citations, 1)
                ],
            )
        )
    for pair in pending:
        rows.append(
            dict(
                number=len(rows) + 1,
                name=purchases[pair.item_id].name,
                diagnosis=diagnoses[pair.diagnosis_id].name,
                category="не установлена",
                evidence="проверка не выполнена",
                comment="Не хватило ресурсов или не совпали версии источников.",
                established=[],
                uncertain=[],
                questions=[],
                actions=["Повторите проверку с доступными ресурсами."],
                sources=[],
            )
        )
    for row, pair in zip(rows, pairs):
        if "DIAGNOSIS_MISMATCH" in pair.reasons:
            row["comment"] = "Сверьте название диагноза, код МКБ-10 и выбранную редакцию клинических рекомендаций."
        elif pair.explanation is None and {"NEEDS_CONTEXT", "NEEDS_REVIEW"}.intersection(pair.reasons):
            row["comment"] = "Уточните применимую редакцию клинических рекомендаций по коду МКБ-10 и возрасту."
        row["actions"] = list(dict.fromkeys(row["actions"] + pair.staff_actions))
        purchase = purchases[pair.item_id]
        if purchase.currency is None:
            row["comment"] += " Валюта не указана."
            if purchase.amount is not None:
                row["comment"] += " Сумма покупки: " + str(purchase.amount) + "."
        row["comment"] += " " + pair.form_reason
        if pair.form_status == "MISMATCH":
            row["actions"].append("Сверьте концентрацию и лекарственную форму по чеку и реестру.")
        if pair.staff_analysis:
            for field in ("established", "uncertain", "questions"):
                for statement in getattr(pair.staff_analysis, field):
                    entry = dict(
                        text=readable_text(statement.text),
                        sources=[
                            row["sources"][int(ref)]
                            for ref in statement.citation_refs
                            if ref.isdigit() and int(ref) < len(row["sources"])
                        ],
                    )
                    if hasattr(statement, "why"):
                        entry["why"] = readable_text(statement.why)
                    row[field].append(entry)
    return dict(
        schema_version="human-report.1",
        title="Проверка заявки: " + request.request_id,
        notice="Решение о выплате принимает специалист. Назначение лечения не выполняется.",
        summary=f"Обработано пар: {len(pairs)} из {len(request.links)}. Покупок без указанной валюты: {len(costs.unknown_currency_item_ids)}.",
        recommendation_note=(
            "Часть рекомендаций подготовлена в резервном режиме; требуется сверка."
            if any(pair.generation_mode == "FALLBACK" for pair in pairs)
            else "Разбор подготовлен моделью по проверенному пакету фактов; требуется сверка."
            if any(pair.generation_mode == "MODEL" for pair in pairs)
            else "Рекомендации подготовлены по доступным сведениям."
        ),
        review_note="Финальная сверка специалистом обязательна; нерешённые основания отмечены отдельно.",
        items=rows,
        costs=[
            dict(amount=str(amount), currency=currency, currency_label=currency, unknown_amount_count=0)
            for currency, amount in costs.totals.items()
        ],
        purchases_without_amount_count=len(costs.unknown_amount_item_ids),
    )
