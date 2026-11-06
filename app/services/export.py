"""Выгрузки из одного сохранённого HTTP-контракта."""

from html import escape
from io import BytesIO
from urllib.parse import urlsplit

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.schemas.demo_result import ApplicationResult

COLORS = {
    "green": "Зелёный", "yellow": "Жёлтый", "red": "Красный",
    "undetermined": "Требуется сверка",
}
STATUS = {
    "SUPPORTED": "Подтверждено для демо", "INSUFFICIENT_DATA": "Недостаточно данных",
    "UNCONFIRMED": "Основание не подтверждено", "SOURCE_CONFLICT": "Конфликт источников",
    "PROCESSING_ERROR": "Ошибка проверки",
}
RECOMMENDATION = (
    "Секретарю комиссии: сверьте МНН, исходные суммы и нерешённые позиции. "
    "Передайте результаты по каждому диагнозу специалисту для ручной проверки."
)
DEMO_NOTICE = "Категории А/Б/В и пороги индикатора — демонстрационные правила. Решение о выплате принимает комиссия."
OFFICIAL_NOTICE = "Официальные источники приведены для сверки. Категории определяются правилами комиссии."


def safe_https(url: str | None) -> str | None:
    if url:
        parsed = urlsplit(url)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username:
            return url
    return None


def _append(sheet, values):
    sheet.append(values)
    # Включая строки, начинающиеся с «=», и текст, выглядящий как код ошибки Excel.
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"


def _format(sheet, widths):
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="174D4C")
        cell.font = Font(color="FFFFFF", bold=True)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _status(row):
    if row.reason_code.startswith("OFFICIAL_"):
        if row.category:
            return "Правило применено"
        return "Требуется сверка" if row.relationship_found else row.explanation
    return STATUS[row.evidence_status]


def build_xlsx(result: ApplicationResult) -> bytes:
    workbook = Workbook()
    application = workbook.active
    application.title = "Заявка"
    checks = workbook.create_sheet("Проверка")
    sources = workbook.create_sheet("Источники")
    summary = workbook.create_sheet("Сводка")
    names = {diag.diagnosis_id: f"{diag.name} · строка {diag.source_row}" if diag.source_row else diag.name
             for diag in result.diagnoses}
    items = {item.item_id: item for item in result.items}
    _append(application, ["Строка", "Препарат", "МНН", "Диагноз (ячейка)", "Дозировка",
                          "Количество (ячейка)", "Сумма (ячейка)", "Сумма учтена, ₽", "Диагноз блока"])
    for item in result.items:
        _append(application, [item.row_number, item.original_cells.get("drug_name"), item.mnn,
                              item.original_cells.get("diagnosis"), item.original_cells.get("dosage"),
                              item.original_cells.get("quantity"), item.original_cells.get("amount"),
                              item.amount, item.diagnosis])
        application.cell(application.max_row, 8).number_format = '#,##0.00'
    _append(checks, ["Строка", "Препарат", "Диагноз", "Категория", "Основание",
                     "Код причины", "Пояснение", "Ручная проверка", "Фрагмент", "Режим", "Поиск", "Резерв",
                     "Условия из источника", "Правила", "Фрагмент по выбору GigaChat"])
    _append(sources, ["Строка", "Диагноз", "Фрагмент", "Источник", "Версия", "Раздел",
                      "Цитата", "URL", "Издатель", "Дата получения", "Назначение"])
    for row in result.checks:
        item = items[row.item_id]
        status = _status(row)
        _append(checks, [item.row_number, item.drug_name, names[row.diagnosis_id], row.category,
                         status, row.reason_code, row.explanation,
                         "Да" if row.needs_review else "Нет", row.fragment_id, row.effective_mode,
                         row.retrieval, row.fallback_reason, "\n".join(row.evidence_conditions),
                         ", ".join(row.rule_ids), row.model_highlight.fragment_id if row.model_highlight else None])
        for citation, purpose in (
            [(c, "Подтверждение") for c in row.citations]
            + [(c, "Материал для сверки") for c in row.review_sources]
        ):
            _append(sources, [item.row_number, names[row.diagnosis_id], citation.fragment_id,
                              citation.title, citation.version, citation.location, citation.quote,
                              safe_https(citation.url), citation.publisher, citation.retrieved_at, purpose])
            if safe_https(citation.url):
                sources.cell(sources.max_row, 8).hyperlink = citation.url
    _append(summary, ["Диагноз / показатель", "Индикатор / значение", "Причина",
                      "А, ₽", "Б, ₽", "В, ₽", "Не определено, ₽",
                      "А, %", "Б, %", "В, %", "Не определено, %"])
    for row in result.summary_by_diagnosis:
        labels = ("А", "Б", "В", "undetermined")
        _append(summary, [names[row.diagnosis_id], COLORS[row.traffic_light] if result.is_demo else "", row.reason,
                          *(row.amounts[key] for key in labels), *(row.shares[key] for key in labels)])
        for cell in summary[summary.max_row][3:]:
            cell.number_format = '#,##0.00'
    if result.amounts_total:
        _append(summary, ["Итого по категориям", "", "",
                          *(result.amounts_total[key] for key in ("А", "Б", "В", "undetermined"))])
    if result.recommendation:
        _append(summary, ["Рекомендация секретарю", result.recommendation])
    for citation in result.model_summary:
        _append(summary, ["Фрагмент по выбору GigaChat", citation.title, citation.quote, citation.url])
    if result.sheet_name:
        _append(summary, ["Лист XLSX", result.sheet_name])
    _append(summary, ["Известная сумма заявки, ₽", result.known_total])
    summary.cell(summary.max_row, 2).number_format = '#,##0.00'
    _append(summary, ["Справочник", result.reference_version])
    _append(summary, ["Корпус", result.corpus_version])
    _append(summary, ["Режим", result.effective_mode, result.fallback_reason])
    if result.reference_profile == "official":
        _append(summary, ["ГРЛС на дату", result.official_as_of])
        registry = workbook.create_sheet("Состав и реестр")
        _append(registry, ["Строка", "Тип записи", "РУ", "Форма", "Выдержка", "Источник", "URL"])
        for item in result.items:
            if item.official_drug is None:
                continue
            for title, rows in (("ГРЛС", item.official_drug.registrations),
                                ("Состав", item.official_drug.compositions)):
                for row in rows:
                    _append(registry, [item.row_number, title, row.registration, row.form,
                                      row.source.quote, row.source.title, safe_https(row.source.url)])
                    if safe_https(row.source.url):
                        registry.cell(registry.max_row, 7).hyperlink = row.source.url
        _format(registry, [10, 18, 26, 32, 60, 36, 42])
    _append(summary, ["Примечание", DEMO_NOTICE if result.is_demo else OFFICIAL_NOTICE])
    _append(summary, ["Приписка", result.disclaimer])
    for issue in result.issues:
        _append(summary, [f"Замечание, строка {issue.row}" if issue.row else "Замечание",
                          issue.code, issue.message])
    _format(application, [10, 24, 20, 36, 18, 20, 20, 18, 34])
    _format(checks, [10, 24, 34, 12, 36, 28, 55, 16, 23, 16, 16, 25, 52, 26, 32])
    _format(sources, [10, 34, 22, 35, 22, 25, 58, 40, 20, 18, 23])
    _format(summary, [34, 25, 55, 17, 17, 17, 19, 12, 12, 12, 19])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_html(result: ApplicationResult) -> str:
    def e(value) -> str:
        return escape(str(value) if value is not None else "—")

    def cite(row):
        url = safe_https(row.url)
        title = f'<a href="{e(url)}">{e(row.title)}</a>' if url else e(row.title)
        return (f"<li><blockquote>{e(row.quote)}</blockquote>{title} · {e(row.version)} · "
                f"{e(row.location)} · {e(row.fragment_id)} · получено {e(row.retrieved_at)}</li>")

    names = {diag.diagnosis_id: f"{diag.name} · строка {diag.source_row}" if diag.source_row else diag.name
             for diag in result.diagnoses}
    items = {item.item_id: item for item in result.items}
    sections = []
    for summary in result.summary_by_diagnosis:
        rows = []
        for check in result.checks:
            if check.diagnosis_id != summary.diagnosis_id:
                continue
            item = items[check.item_id]
            citations = "".join(cite(c) for c in check.citations)
            source_list = f"<ul>{citations}</ul>" if citations else ""
            if check.evidence_conditions:
                source_list += "<p>" + "<br>".join(e(x) for x in check.evidence_conditions) + "</p>"
            if check.review_sources:
                review = "".join(cite(c) for c in check.review_sources)
                source_list += f"<p>Материалы для сверки</p><ul>{review}</ul>"
            fallback = f" · резерв {e(check.fallback_reason)}" if check.fallback_reason else ""
            status = _status(check)
            if check.model_highlight:
                source_list += f"<p>Фрагмент, выделенный GigaChat</p><ul>{cite(check.model_highlight)}</ul>"
            if check.rule_ids:
                source_list += f"<p>Правила {e(', '.join(check.rule_ids))}</p>"
            rows.append(f"<tr><td>{item.row_number}</td><td>{e(item.drug_name)}<br>{e(item.mnn)}</td>"
                        f"<td>{e(item.amount)}</td><td>{e(check.category)}</td>"
                        f"<td>{e(status)} · {e(check.reason_code)}<br>"
                        f"{e(check.explanation)}{source_list}"
                        f"<small>{e(check.effective_mode)} · {e(check.retrieval)}{fallback}</small></td></tr>")
        amounts = " · ".join(f"{e('Не определено' if label == 'undetermined' else label)} "
                             f"{e(amount)} ₽ ({e(summary.shares[label])} %)"
                             for label, amount in summary.amounts.items())
        if result.is_demo:
            pair_status = f"Подтверждено {summary.confirmed_count} из {summary.pair_count} пар."
        else:
            found = sum(check.relationship_found for check in result.checks
                        if check.diagnosis_id == summary.diagnosis_id)
            pair_status = f"Документы найдены для {found} из {summary.pair_count} пар."
        indicator = (f"<p><strong>{e(COLORS[summary.traffic_light])}</strong> — {e(summary.reason)}</p>"
                     if result.is_demo else "")
        sections.append(
            f"<section><h2>{e(names[summary.diagnosis_id])}</h2>{indicator}"
            f"<p>{amounts}</p><p>{pair_status}</p>"
            "<table><thead><tr><th>Строка</th><th>Препарат / МНН</th><th>Сумма, ₽</th>"
            "<th>Категория</th><th>Проверка и источники</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )
    issues = "".join(f"<li>{'Строка ' + str(i.row) + ' · ' if i.row else ''}{e(i.message)}</li>"
                     for i in result.issues)
    known = f"{result.known_total:,.2f}".replace(",", " ").replace(".", ",")
    official = ""
    if result.reference_profile == "official":
        reg_parts = []
        for item in result.items:
            if not item.official_drug or not item.official_drug.registration_count:
                continue
            citations = "".join(
                f"<li>РУ {e(row.registration)} · {e(row.form)}<ul>{cite(row.source)}</ul></li>"
                for row in (*item.official_drug.registrations, *item.official_drug.compositions)
            )
            reg_parts.append(f"<details><summary>Строка {item.row_number} · {e(item.drug_name)}</summary>"
                             f"<ul>{citations}</ul></details>")
        official = f"<section><h2>Реестр и состав</h2><p>ГРЛС на {e(result.official_as_of)}</p>{''.join(reg_parts)}</section>"
    recommendation = ""
    if result.recommendation:
        totals = " · ".join(f"{e('На сверке' if label == 'undetermined' else label)} {e(amount)} ₽"
                            for label, amount in result.amounts_total.items())
        selected = "".join(cite(row) for row in result.model_summary)
        highlights = f"<h3>Ключевые основания по выбору GigaChat</h3><ul>{selected}</ul>" if selected else ""
        recommendation = (f"<section><h2>Рекомендация секретарю</h2><p>{e(result.recommendation)}</p>"
                          f"<p>{totals}</p>{highlights}</section>")
    return f"""<!doctype html>
<html lang="ru"><meta charset="utf-8"><title>Отчёт по заявке</title>
<style>
body{{font:16px system-ui;color:#172b32;max-width:1100px;margin:36px auto;padding:0 18px}}
h1,h2{{color:#174d4c}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #d5e0df;padding:9px;text-align:left;vertical-align:top}}
th{{background:#eaf3f1}}section{{margin:30px 0}}blockquote{{margin:8px 0}}small{{color:#41565e}}
</style>
<body><h1>{"Демонстрационный отчёт" if result.is_demo else "Отчёт по официальным источникам"}</h1>
<p>Файл {e(result.filename)} · заявка {e(result.application_id)}{f" · лист {e(result.sheet_name)}" if result.sheet_name else ""}</p>
<p><strong>Известная сумма {known} ₽</strong> · режим {e(result.effective_mode)}
· справочник {e(result.reference_version)} · корпус {e(result.corpus_version)}</p>
<p>{e(DEMO_NOTICE if result.is_demo else OFFICIAL_NOTICE)}</p>
{f'<p>Причина резерва · {e(result.fallback_reason)}</p>' if result.fallback_reason else ''}
{f'<h2>Замечания к файлу</h2><ul>{issues}</ul>' if issues else ''}
{recommendation}
{''.join(sections)}
{official}
<footer><p>{e(result.disclaimer)}</p></footer></body></html>"""
