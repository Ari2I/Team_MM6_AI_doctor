"""Human-readable warnings, including previously saved technical messages."""

import re

from app.config import MODEL_ERROR_LABELS, REVIEW_REASON_LABELS

UNKNOWN_REVIEW_REASON = "Причина требует уточнения специалистом."
_TECHNICAL_CODE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


def review_reason_label(reason: str | None) -> str:
    return REVIEW_REASON_LABELS.get(reason or "", UNKNOWN_REVIEW_REASON)


def position_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "позиции"
    return "позиций"


def humanize_warning_text(text: str | None) -> str | None:
    """Format presentation text; machine fields, sources and stored data stay intact."""
    if not text:
        return text
    # The quoted clinical text is evidence, not a generated warning label.
    text, quote_marker, citations = text.partition(" Цитата: ")
    # Older worker comments included configuration details intended for operators.
    text = text.replace(
        "Получение КР выключено: AI_LIVE_SOURCES=false.",
        "Поиск клинических рекомендаций отключён в настройках.",
    )
    text = text.replace(
        "Проверьте наличие GIGACHAT_CREDENTIALS в окружении API.",
        "Обратитесь к администратору для настройки подключения к ИИ.",
    )
    text = text.replace(
        "В образе API отсутствует SDK GigaChat; нужен образ ai-network.",
        "Компонент ИИ недоступен. Обратитесь к администратору.",
    )
    labels = REVIEW_REASON_LABELS | MODEL_ERROR_LABELS
    text = _TECHNICAL_CODE.sub(
        lambda match: labels.get(match[0], UNKNOWN_REVIEW_REASON).rstrip("."), text
    )
    text = re.sub(r"Для (\d+) позиций", lambda m: f"Для {m[1]} {position_word(int(m[1]))}", text)
    return text + quote_marker + citations
