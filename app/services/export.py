"""Выгрузки из одного сохранённого HTTP-контракта."""

from html import escape
from io import BytesIO
from textwrap import wrap
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
MONEY_FORMAT = "#,##0.00"
CHECK_WIDTHS = [9, 24, 22, 29, 14, 13, 49, 34, 31, 15]


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


def _format(sheet, widths, header_row=1):
    sheet.freeze_panes = f"A{header_row + 1}"
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(sheet.max_column)}{sheet.max_row}"
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for cell in sheet[header_row]:
        cell.fill = PatternFill("solid", fgColor="174D4C")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in sheet.iter_rows(min_row=header_row + 1):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _status(row):
    if row.reason_code.startswith("OFFICIAL_"):
        if row.category:
            return "Правило применено"
        return "Требуется сверка" if row.relationship_found else row.explanation
    return STATUS[row.evidence_status]


def _set_link(cell, url):
    if target := safe_https(url):
        cell.hyperlink = target
        cell.font = Font(color="176B82", underline="single")


def _report_line(sheet, text, *, title=False):
    _append(sheet, [text])
    row = sheet.max_row
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(CHECK_WIDTHS))
    cell = sheet.cell(row, 1)
    cell.alignment = Alignment(vertical="center", wrap_text=True)
    if title:
        cell.fill = PatternFill("solid", fgColor="174D4C")
        cell.font = Font(color="FFFFFF", bold=True, size=16)
    lines = sum(max(1, len(wrap(line, width=150))) for line in text.split("\n"))
    sheet.row_dimensions[row].height = max(29 if title else 21, 15 * lines + 6)


def _official_report_header(sheet, result):
    _report_line(sheet, "Отчёт по проверке расходов", title=True)
    _report_line(sheet, f"Файл заявки · {result.filename}")
    _append(sheet, [None, "Известная сумма, ₽", "Категория А, ₽", "Категория Б, ₽",
                    "Категория В, ₽", "На сверке, ₽"])
    _append(sheet, [None, result.known_total, *(result.amounts_total.get(key)
                                             for key in ("А", "Б", "В", "undetermined"))])
    for label, amount in zip(sheet[3][1:6], sheet[4][1:6]):
        label.fill = PatternFill("solid", fgColor="EAF3F1")
        label.font = Font(color="174D4C", bold=True)
        label.alignment = Alignment(vertical="center", wrap_text=True)
        amount.font = Font(bold=True, size=13)
        amount.number_format = MONEY_FORMAT
    sheet.row_dimensions[3].height = 31
    found = sum(row.relationship_found for row in result.checks)
    _report_line(sheet, f"Проверок · {len(result.checks)}    "
                        f"Связей в справочнике · {found}    "
                        f"Замечаний к файлу · {len(result.issues)} (лист «Сводка»)\n"
                        "Полный список документов и выдержки приведены на листе «Источники».")
    _report_line(sheet, f"Рекомендация секретарю. {result.recommendation or OFFICIAL_NOTICE}")
    if result.model_review_priorities:
        _report_line(sheet, "Что сверить в первую очередь по выбору GigaChat\n"
                            + "\n".join(result.model_review_priorities))
    _append(sheet, ["Строка", "Препарат", "МНН", "Диагноз блока", "Сумма, ₽", "Категория", "Основание",
                    "Шаг сверки GigaChat", "Документ и его роль", "Копия",
                    "Код причины", "Ручная проверка", "Фрагмент", "Режим", "Поиск", "Резерв",
                    "Условия из источника", "Правила", "Фрагмент по выбору GigaChat"])
    sheet.row_dimensions[sheet.max_row].height = 30
    return sheet.max_row


def _official_report_check(sheet, row, item, diagnosis, status, references, details):
    citation, purpose = references[0] if references else (None, None)
    document = f"{purpose}\n{citation.title} · {citation.version}" if citation else None
    explanation = row.explanation if status == row.explanation else f"{status}\n{row.explanation}"
    copy_url = safe_https(citation.copy_url) if citation else None
    amount = item.amount if item.amount is not None else "Не учтена"
    _append(sheet, [item.row_number, item.drug_name, item.mnn, diagnosis, amount,
                    row.category or "На сверке", explanation, row.model_review_action, document,
                    "Открыть копию" if copy_url else None, *details])
    number = sheet.max_row
    sheet.cell(number, 5).number_format = MONEY_FORMAT
    if citation:
        _set_link(sheet.cell(number, 9), citation.url)
        _set_link(sheet.cell(number, 10), copy_url)
    visible = sheet[number][:len(CHECK_WIDTHS)]
    lines = max(sum(max(1, len(wrap(line, width=width - 2))) for line in str(cell.value or "").split("\n"))
                for cell, width in zip(visible, CHECK_WIDTHS))
    sheet.row_dimensions[number].height = min(409, max(28, 15 * lines + 6))


def build_xlsx(result: ApplicationResult) -> bytes:
    workbook = Workbook()
    official = result.reference_profile == "official"
    application = workbook.active
    application.title = "Заявка"
    checks = workbook.create_sheet("Проверка", 0 if official else None)
    sources = workbook.create_sheet("Источники")
    summary = workbook.create_sheet("Сводка", 1 if official else None)
    if official:
        workbook.active = checks
        workbook.move_sheet(application, offset=1)
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
    if official:
        header_row = _official_report_header(checks, result)
    else:
        _append(checks, ["Строка", "Препарат", "Диагноз", "Категория", "Основание",
                         "Код причины", "Пояснение", "Ручная проверка", "Фрагмент", "Режим", "Поиск", "Резерв",
                         "Условия из источника", "Правила", "Фрагмент по выбору GigaChat", "Шаг сверки по выбору GigaChat"])
    _append(sources, ["Строка", "Диагноз", "Фрагмент", "Источник", "Версия", "Раздел",
                      "Цитата", "URL оригинала", "Издатель", "Дата получения", "Назначение", "URL опубликованной копии"])
    for row in result.checks:
        item = items[row.item_id]
        status = _status(row)
        details = ["Да" if row.needs_review else "Нет", row.fragment_id, row.effective_mode,
                   row.retrieval, row.fallback_reason, "\n".join(row.evidence_conditions),
                   ", ".join(row.rule_ids), row.model_highlight.fragment_id if row.model_highlight else None]
        references = ([(c, "Подтверждение") for c in row.citations]
                      + [(c, "Материал для сверки") for c in row.review_sources]
                      + [(c, "Документ, по которому выполнен поиск") for c in row.searched_sources]
                      + [(c, "Карточка каталога без доступного текста") for c in row.catalog_sources])
        diagnosis = names[row.diagnosis_id]
        if official:
            _official_report_check(checks, row, item, diagnosis, status, references, [row.reason_code, *details])
        else:
            _append(checks, [item.row_number, item.drug_name, diagnosis, row.category,
                             status, row.reason_code, row.explanation, *details, row.model_review_action])
        for citation, purpose in references:
            _append(sources, [item.row_number, names[row.diagnosis_id], citation.fragment_id,
                              citation.title, citation.version, citation.location, citation.quote,
                              safe_https(citation.url), citation.publisher, citation.retrieved_at, purpose,
                              safe_https(citation.copy_url)])
            _set_link(sources.cell(sources.max_row, 8), citation.url)
            _set_link(sources.cell(sources.max_row, 12), citation.copy_url)
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
    for action in result.model_review_priorities:
        _append(summary, ["Приоритет сверки по выбору GigaChat", action])
    if result.model_tokens is not None:
        _append(summary, ["Токены GigaChat", result.model_tokens])
    for citation in result.model_summary:
        _append(summary, ["Фрагмент по выбору GigaChat", citation.title, citation.quote, citation.url])
    if result.sheet_name:
        _append(summary, ["Лист XLSX", result.sheet_name])
    _append(summary, ["Известная сумма заявки, ₽", result.known_total])
    summary.cell(summary.max_row, 2).number_format = '#,##0.00'
    _append(summary, ["Справочник", result.reference_version])
    _append(summary, ["Корпус", result.corpus_version])
    _append(summary, ["Режим", result.effective_mode, result.fallback_reason])
    if official:
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
    if official:
        _format(checks, [*CHECK_WIDTHS, 30, 16, 23, 16, 16, 25, 52, 26, 32], header_row)
        checks.column_dimensions.group("K", "S", hidden=True)
        checks.sheet_view.zoomScale = 85
    else:
        _format(checks, [10, 24, 34, 12, 36, 28, 55, 16, 23, 16, 16, 25, 52, 26, 32, 45])
    _format(sources, [10, 34, 22, 35, 22, 25, 58, 40, 20, 18, 29, 42])
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
        copy_url = safe_https(row.copy_url)
        copy = f' · <a href="{e(copy_url)}">Опубликованная копия</a> для сверки' if copy_url else ""
        return (f"<li><blockquote>{e(row.quote)}</blockquote>{title} · {e(row.version)} · "
                f"{e(row.location)} · {e(row.fragment_id)} · получено {e(row.retrieved_at)}{copy}</li>")

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
            if check.searched_sources:
                searched = "".join(cite(c) for c in check.searched_sources)
                source_list += f"<p>Документы, по которым выполнен поиск</p><ul>{searched}</ul>"
            if check.catalog_sources:
                catalog = "".join(cite(c) for c in check.catalog_sources)
                source_list += f"<p>Рекомендации в каталоге без доступного текста</p><ul>{catalog}</ul>"
            if check.review_sources:
                review = "".join(cite(c) for c in check.review_sources)
                source_list += f"<p>Материалы для сверки</p><ul>{review}</ul>"
            fallback = f" · резерв {e(check.fallback_reason)}" if check.fallback_reason else ""
            status = _status(check)
            if check.model_highlight:
                source_list += f"<p>Фрагмент, выделенный GigaChat</p><ul>{cite(check.model_highlight)}</ul>"
            if check.model_review_action:
                source_list += f"<p>Шаг сверки по выбору GigaChat. {e(check.model_review_action)}</p>"
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
    priorities = "".join(f"<li>{e(action)}</li>" for action in result.model_review_priorities)
    if priorities:
        recommendation += f"<section><h2>Что сверить в первую очередь</h2><ul>{priorities}</ul></section>"
    token_status = f"<p>Токены GigaChat · {e(result.model_tokens)}</p>" if result.model_tokens is not None else ""
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
{token_status}
{recommendation}
{''.join(sections)}
{official}
<footer><p>{e(result.disclaimer)}</p></footer></body></html>"""
