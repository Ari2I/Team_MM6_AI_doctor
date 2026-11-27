"""Кооперативные лимиты одной заявки; сетевые повторы по умолчанию выключены."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Literal


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
    _unsettled_calls: int = 0

    @property
    def token_accounting(self) -> Literal["UPPER_BOUND", "ACTUAL"]:
        return "UPPER_BOUND" if self._unsettled_calls else "ACTUAL"

    def reserve(self, tokens):
        remaining = self.deadline - self.clock()
        if self.exhausted or remaining <= 0 or self.calls >= self.max_calls or self.tokens + tokens > self.max_tokens:
            self.exhausted = True
            raise BudgetExhausted("Бюджет заявки исчерпан.")
        self.calls += 1
        self.tokens += tokens
        self._unsettled_calls += 1
        return remaining

    @contextmanager
    def attempt(self, tokens: int, *, usage: Callable[[], int | None]) -> Iterator[float]:
        """Reserve before invocation, settle only a successful current API receipt.

        Unknown/failed calls keep their full reservation. Settling never restores
        attempts or time, and an unexpected actual overrun blocks later calls.
        """
        remaining = self.reserve(tokens)
        yield remaining
        actual = usage()
        if type(actual) is int and actual >= 0:
            self.tokens += actual - tokens
            self._unsettled_calls -= 1
            if self.tokens > self.max_tokens:
                self.exhausted = True
