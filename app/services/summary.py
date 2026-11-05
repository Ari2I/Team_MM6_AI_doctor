"""Чистые функции агрегации результатов анализа."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any, NamedTuple

from app.config import get_traffic_light_settings
from app.models.enums import DrugCategory, TrafficLight

_ORDER = (DrugCategory.A, DrugCategory.B, DrugCategory.V)


def _value(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, Mapping) else getattr(obj, name, default)


def best_category(checks: Iterable[Any]) -> DrugCategory | None:
    """Вернуть лучшую категорию строки по приоритету A > B > V."""
    categories = {_value(check, "category") for check in checks}
    for category in _ORDER:
        if category in categories or category.value in categories:
            return category
    return None


def category_shares(items_with_best_category: Iterable[Any]) -> dict[str, float]:
    """Посчитать суммы и процентные доли; суммы доступны в ключах ``*_amount``."""
    amounts: dict[str, Decimal] = {category.value: Decimal("0") for category in _ORDER}
    amounts["undetermined"] = Decimal("0")
    for item in items_with_best_category:
        amount = _value(item, "amount")
        if amount is None:
            continue
        try:
            amount_decimal = Decimal(str(amount))
        except Exception:
            continue
        if amount_decimal <= 0:
            continue
        category = _value(item, "best_category", _value(item, "category"))
        key = (
            category.value
            if isinstance(category, DrugCategory)
            else str(category)
            if category
            else "undetermined"
        )
        if key not in amounts:
            key = "undetermined"
        amounts[key] += amount_decimal
    total = sum(amounts.values(), Decimal("0"))
    result = {
        key: float((value / total * 100) if total else Decimal("0"))
        for key, value in amounts.items()
    }
    result.update({f"{key}_amount": float(value) for key, value in amounts.items()})
    result["total_amount"] = float(total)
    return result


class TrafficLightDecision(NamedTuple):
    color: TrafficLight
    reason: str


def compute_traffic_light(shares: Mapping[str, float], has_review: bool) -> TrafficLightDecision:
    settings = get_traffic_light_settings()
    a_share = float(shares.get("A", 0))
    unknown_share = float(shares.get("undetermined", 0))
    if "total_amount" in shares and float(shares.get("total_amount", 0)) <= 0:
        return TrafficLightDecision(
            TrafficLight.UNDETERMINED,
            "Нет положительных сумм для расчёта долей; требуется проверка человеком.",
        )
    if unknown_share >= settings.undetermined_share_threshold:
        return TrafficLightDecision(
            TrafficLight.UNDETERMINED,
            "Доля неопределённых строк "
            f"{unknown_share:.2f}% не ниже порога "
            f"{settings.undetermined_share_threshold:.2f}%.",
        )
    if a_share > settings.green_category_a_threshold:
        if has_review:
            return TrafficLightDecision(
                TrafficLight.YELLOW,
                "Доля категории A выше порога, но есть позиции "
                "на проверку человеком "
                f"({a_share:.2f}%).",
            )
        return TrafficLightDecision(
            TrafficLight.GREEN,
            "Доля категории A "
            f"{a_share:.2f}% выше порога "
            f"{settings.green_category_a_threshold:.2f}%.",
        )
    # TODO: уточнить у аналитика остальные правила жёлтого и красного.
    if has_review:
        return TrafficLightDecision(
            TrafficLight.YELLOW, "Есть позиции, требующие проверки человеком."
        )
    return TrafficLightDecision(
        TrafficLight.RED, "Доля категории A не превышает установленный порог."
    )


def build_summary(
    shares: Mapping[str, float], traffic_light: TrafficLight | str, has_review: bool = False
) -> str:
    color = traffic_light.value if isinstance(traffic_light, TrafficLight) else str(traffic_light)
    return (
        f"Результат анализа: индикатор {color}; категория A — {float(shares.get('A', 0)):.2f}%, "
        f"B — {float(shares.get('B', 0)):.2f}%, V — {float(shares.get('V', 0)):.2f}%, "
        f"не определена — {float(shares.get('undetermined', 0)):.2f}%. "
        + ("Требуется проверка человеком. " if has_review else "")
        + "Результат носит рекомендательный характер; решение принимает человек."
    )


def build_warning_message(checks: Iterable[Any]) -> str | None:
    review_checks = [check for check in checks if _value(check, "needs_review", False)]
    if not review_checks:
        return None
    item_ids = {_value(check, "item_id") for check in review_checks}
    count = len(item_ids - {None}) or len(review_checks)
    reasons = sorted(
        {
            str(_value(check, "review_reason"))
            for check in review_checks
            if _value(check, "review_reason")
        }
    )
    suffix = ", ".join(reasons) if reasons else "требуется уточнение"
    return f"Для {count} позиций требуется проверка человеком: {suffix}."
