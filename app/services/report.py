"""Форматирование сохранённой карточки заявки в XLSX без повторного анализа."""

from datetime import UTC
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from app.config import CATEGORY_LABELS, REVIEW_REASON_LABELS, TRAFFIC_LIGHT_COLORS
from app.schemas.application_api import ApplicationCard

_HEADER_FILL = PatternFill("solid", fgColor="D9E2F3")
_REVIEW_FILL = PatternFill("solid", fgColor="FFF2CC")
_TRAFFIC_LABELS = {
    "green": "Зелёный",
    "yellow": "Жёлтый",
    "red": "Красный",
    "undetermined": "Не определён",
}
_LEVEL_LABELS = {"warning": "Предупреждение", "critical": "Критическое замечание"}


def _append(sheet: Worksheet, values: list[Any]) -> None:
    # Апостроф сохраняется в файле: пользовательский текст никогда не станет формулой.
    sheet.append(
        [
            "'" + value
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
            else value
            for value in values
        ]
    )


def _format_sheet(sheet: Worksheet) -> None:
    sheet.freeze_panes = "A2"
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
    for column in sheet.columns:
        width = max(
            (
                max((len(line) for line in str(cell.value or "").splitlines()), default=0)
                for cell in column
            ),
            default=0,
        )
        # Длинные тексты переносятся, чтобы столбец не занимал весь экран.
        sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 12), 70)
        for cell in column:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def build_report(card: ApplicationCard | dict[str, Any]) -> bytes:
    """Принять карточку GET /applications/{id} (модель или JSON-словарь)."""
    card = ApplicationCard.model_validate(card)
    application = card.application
    workbook = Workbook()
    summary = workbook.active
    assert summary is not None
    summary.title = "Сводка"
    _append(summary, ["Поле", "Значение"])
    _append(summary, ["Имя файла", application.filename])
    # Excel не поддерживает datetime с часовым поясом; сохраняем ISO-строку в UTC.
    _append(summary, ["Дата создания заявки", application.created_at.astimezone(UTC).isoformat()])
    color = application.traffic_light or "undetermined"
    _append(summary, ["Светофор", _TRAFFIC_LABELS[color]])
    summary["B4"].fill = PatternFill("solid", fgColor=TRAFFIC_LIGHT_COLORS[color])
    _append(summary, ["Диагнозы заявки", "\n".join(card.diagnoses)])
    shares = application.category_shares or {}
    for category in ("A", "B", "V", "undetermined"):
        _append(summary, [f"Доля суммы: {CATEGORY_LABELS[category]}", shares.get(category)])
        summary.cell(summary.max_row, 2).number_format = '0.00"%"'
    _append(summary, ["Резюме", application.summary])
    if application.warning_message:
        _append(summary, ["Предупреждение", application.warning_message])
    versions = sorted(
        {
            check.reference_version
            for item in card.items
            for check in item.checks
            if check.reference_version
        }
    )
    _append(summary, ["Версия справочника", "\n".join(versions) or "Не указана"])
    # В карточке нет is_synthetic; импортёр допускает только Literal[True].
    # Если появятся несинтетические версии, карточке потребуются их метаданные.
    _append(
        summary,
        [
            "Справочник",
            "Демонстрационный (синтетический); не является клиническим справочником.",
        ],
    )
    _append(summary, ["Дисклеймер", card.disclaimer])

    drugs = workbook.create_sheet("Препараты")
    _append(
        drugs,
        [
            "Строка исходного файла",
            "Название препарата",
            "МНН",
            "Диагноз",
            "Категория",
            "Статус проверки",
            "Причина",
            "Источник",
            "Версия справочника",
            "Комментарий",
        ],
    )
    for item in card.items:
        for check in item.checks or [None]:
            needs_review = check is None or check.needs_review
            reason = (
                REVIEW_REASON_LABELS.get(check.review_reason, "Причина требует уточнения.")
                if check and check.review_reason
                else "Нет сохранённых проверок пар препарат — диагноз."
                if check is None
                else "Требуется проверка человеком."
                if needs_review
                else None
            )
            _append(
                drugs,
                [
                    item.row_number,
                    item.drug_name,
                    item.mnn,
                    check.diagnosis if check else None,
                    CATEGORY_LABELS[check.category if check and check.category else "undetermined"],
                    "Требует проверки" if needs_review else "Проверено правилом",
                    reason,
                    check.source if check else None,
                    check.reference_version if check else None,
                    check.comment if check else None,
                ],
            )
            if needs_review:
                for cell in drugs[drugs.max_row]:
                    cell.fill = _REVIEW_FILL
    drugs.auto_filter.ref = drugs.dimensions

    warnings = workbook.create_sheet("Предупреждения")
    _append(warnings, ["Уровень", "Сообщение", "Строка", "Столбец"])
    for issue in card.issues:
        _append(
            warnings,
            [
                _LEVEL_LABELS.get(issue.level, issue.level),
                issue.message,
                issue.row,
                issue.column,
            ],
        )
    for sheet in workbook:
        _format_sheet(sheet)
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()
