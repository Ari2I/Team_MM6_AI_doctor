"""Общий интерфейс генератора и безопасная техническая ошибка."""

from typing import Protocol


PROVIDER_ERROR_MESSAGES = {
    "RESPONSE_ITEM_MISMATCH": "Модель вернула идентификатор другой позиции; ответ отклонён.",
    "INVALID_RESPONSE_SCHEMA": "Модель вернула ответ, который не соответствует контракту проекта.",
    "GIGACHAT_TLS_ERROR": "Не удалось проверить сертификат сервера GigaChat. Укажите доверенный CA через GIGACHAT_CA_BUNDLE_FILE.",
    "GIGACHAT_AUTH_FAILED": "GigaChat отклонил авторизацию. Проверьте ключ или срок действия токена и scope.",
    "GIGACHAT_FORBIDDEN": "Доступ к GigaChat запрещён. Проверьте права проекта и выбранную модель.",
    "GIGACHAT_NOT_FOUND": "GigaChat не нашёл запрошенный ресурс. Проверьте доступность модели.",
    "GIGACHAT_BAD_REQUEST": "GigaChat отклонил параметры запроса или авторизации.",
    "GIGACHAT_RATE_LIMIT": "Превышен лимит запросов GigaChat. Повторите проверку позже.",
    "GIGACHAT_SERVER_ERROR": "Сбой на стороне GigaChat. Повторите проверку позже.",
    "GIGACHAT_TIMEOUT": "Истекло время ожидания ответа GigaChat.",
    "GIGACHAT_UNAVAILABLE": "Не удалось соединиться с GigaChat. Проверьте сеть и настройки прокси.",
    "GIGACHAT_API_ERROR": "SDK GigaChat сообщил об ошибке API.",
    "GIGACHAT_INVALID_RESPONSE": "Ответ GigaChat не соответствует ожидаемому формату SDK.",
    "GIGACHAT_INVALID_JSON": "GigaChat вернул некорректный JSON.",
    "GIGACHAT_INVALID_JSON_OBJECT": "GigaChat вернул JSON неподходящего типа.",
    "GIGACHAT_INVALID_COMPLETION": "GigaChat вернул неподходящую структуру сообщения.",
    "GIGACHAT_INCOMPLETE_COMPLETION": "GigaChat не завершил ответ штатно: возможно ограничение длины или отказ модели.",
    "GIGACHAT_RESPONSE_TOO_LARGE": "Ответ GigaChat превышает допустимый размер.",
}


class ProviderError(Exception):
    """Обезличенная техническая ошибка без данных запроса и учётных данных."""


class Provider(Protocol):
    name: str

    def generate(self, payload: dict) -> dict: ...
