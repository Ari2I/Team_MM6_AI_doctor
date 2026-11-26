"""Провайдеры генерации: общий интерфейс, локальный шаблон и JSON-шлюз."""

from .base import Provider, ProviderError
from .template import TemplateProvider
from .gateway import JsonApiProvider

__all__ = ["Provider", "ProviderError", "TemplateProvider", "JsonApiProvider"]
