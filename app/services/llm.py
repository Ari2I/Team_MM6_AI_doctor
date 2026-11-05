"""Локальный интерфейс текстового провайдера без передачи заявок наружу."""

from typing import Protocol

from app.config import get_llm_settings


class LLMProvider(Protocol):
    def generate(self, prompt: str) -> str:
        """Сгенерировать текст по деперсонифицированному prompt."""


class TemplateProvider:
    """Провайдер по умолчанию: возвращает безопасный шаблонный текст."""

    def generate(self, prompt: str) -> str:
        return prompt


def get_provider() -> LLMProvider:
    # Реальные провайдеры подключаются отдельно; внешний вызов по умолчанию выключен.
    provider_name = get_llm_settings().provider.lower()
    if provider_name != "template":
        # TODO: уточнить у аналитика и подключить разрешённые закрытые адаптеры.
        return TemplateProvider()
    return TemplateProvider()
