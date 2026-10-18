"""Схема ответа проверки работоспособности API."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Состояние API и обязательный дисклеймер о характере анализа."""

    status: Literal["ok"] = "ok"
    disclaimer: str = "Анализ носит рекомендательный характер. Решение принимает человек."
