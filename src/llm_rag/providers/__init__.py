"""Провайдеры генерации: общий интерфейс, локальное демо и JSON-шлюз."""

from .base import Provider, ProviderError
from .demo import DemoProvider
from .gateway import JsonApiProvider

__all__ = ["Provider", "ProviderError", "DemoProvider", "JsonApiProvider"]
