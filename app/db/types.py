"""Числа БД без потери точности; прежние суммы сохраняют два знака в ответах."""

from decimal import Decimal

from sqlalchemy import Numeric
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class ExactAmount(TypeDecorator[Decimal]):
    impl = Numeric
    cache_ok = True

    def process_result_value(self, value: Decimal | None, dialect: Dialect) -> Decimal | None:
        if value is not None and value.as_tuple().exponent > -2:
            return value.quantize(Decimal("0.01"))
        return value
