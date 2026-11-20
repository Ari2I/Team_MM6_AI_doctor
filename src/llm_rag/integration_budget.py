"""Кооперативные лимиты одной заявки; сетевые повторы по умолчанию выключены."""

from dataclasses import dataclass
from time import monotonic
from typing import Callable


def source_timeout(deadline: float | None, maximum: float) -> float:
    """Ограничить очередное чтение источника остатком времени заявки."""
    if deadline is None:
        return maximum
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Истекло время подготовки источников.")
    return min(maximum, remaining)


class BudgetExhausted(RuntimeError):
    """До вызова не осталось времени, токенов или разрешённых попыток."""


@dataclass
class RequestBudget:
    deadline: float
    max_calls: int
    max_tokens: int
    clock: Callable
    calls: int = 0
    tokens: int = 0
    exhausted: bool = False

    def reserve(self, tokens):
        remaining = self.deadline - self.clock()
        if remaining <= 0 or self.calls >= self.max_calls or self.tokens + tokens > self.max_tokens:
            self.exhausted = True
            raise BudgetExhausted("Бюджет заявки исчерпан.")
        self.calls += 1
        self.tokens += tokens
        return remaining
