from dataclasses import dataclass
from typing import Optional
from .contracts import AnalysisItem


@dataclass(frozen=True)
class MedicineReference:
    """Запись демонстрационного справочника лекарств."""

    trade_name: str
    inn: str


@dataclass(frozen=True)
class MatchResult:
    """Результат поиска МНН по торговому названию."""

    inn: Optional[str]
    status: str
    candidates: tuple[str, ...] = ()


def normalize_name(name: str) -> str:
    """Нормализует название для сравнения."""

    return " ".join(name.casefold().split())


def match_trade_name(
    trade_name: str,
    reference: list[MedicineReference],
) -> MatchResult:
    """Ищет точное соответствие торгового названия в справочнике."""

    normalized_name = normalize_name(trade_name)

    if not normalized_name:
        return MatchResult(inn=None, status="INVALID_INPUT")

    matches = {
        entry.inn
        for entry in reference
        if normalize_name(entry.trade_name) == normalized_name
    }

    if not matches:
        return MatchResult(inn=None, status="NOT_FOUND")

    if len(matches) > 1:
        return MatchResult(
            inn=None,
            status="AMBIGUOUS",
            candidates=tuple(sorted(matches)),
        )

    return MatchResult(inn=next(iter(matches)), status="MATCHED")


def match_original(
    original: dict[str, object],
    reference: list[MedicineReference],
) -> dict[str, object]:
    """Подготавливает результат сопоставления для исходной записи."""

    trade_name = original.get("trade_name")

    if not isinstance(trade_name, str):
        return {
            "inn": None,
            "upstream_status": "FAILED",
            "missing_data": ["trade_name"],
            "source_refs": [],
        }

    result = match_trade_name(trade_name, reference)

    if result.status == "MATCHED":
        return {
            "inn": result.inn,
            "upstream_status": "PROCESSED",
            "missing_data": [],
            "source_refs": [],
        }

    if result.status == "AMBIGUOUS":
        return {
            "inn": None,
            "upstream_status": "PARTIAL",
            "missing_data": ["inn"],
            "source_refs": [],
        }

    return {
        "inn": None,
        "upstream_status": "PARTIAL",
        "missing_data": ["inn"],
        "source_refs": [],
    }


def build_analysis_item(
    item_id: str,
    original: dict[str, object],
    reference: list[MedicineReference],
) -> AnalysisItem:
    """Создаёт AnalysisItem с результатом сопоставления торгового названия."""

    match = match_original(original, reference)

    if match["inn"] is None:
        classification_reason = None
        category = None
    else:
        # Найдено МНН, но категорию мы пока не классифицируем.
        classification_reason = None
        category = None

    missing_data = list(match["missing_data"])

    return AnalysisItem(
        item_id=item_id,
        original=original,
        inn=match["inn"],
        category=category,
        classification_reason=classification_reason,
        dosage_check={
            "status": "NOT_CHECKED",
            "reason": None,
            "missing_data": [],
        },
        source_refs=match["source_refs"],
        upstream_status=match["upstream_status"],
        missing_data=missing_data,
    )
