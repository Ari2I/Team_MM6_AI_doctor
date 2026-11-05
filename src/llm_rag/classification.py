from .corpus import Corpus
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


@dataclass(frozen=True)
class CategoryResult:
    """Результат поиска категории в базе."""

    category: Optional[str]
    reason: Optional[str]
    source_refs: tuple[str, ...]
    status: str  # FOUND / NO_RULE / CONFLICT


def classify_category(inn: str, diagnosis: str, corpus: Corpus) -> CategoryResult:
    """Ищет категорию для пары «МНН + диагноз» в корпусе."""

    found = set()
    for fragment in corpus.fragments:
        # Условные записи (нужен контекст) пока пропускаем.
        if fragment.required_context:
            continue
        if normalize_name(fragment.inn) != normalize_name(inn):
            continue
        if normalize_name(fragment.diagnosis) != normalize_name(diagnosis):
            continue
        for assertion in fragment.assertions:
            if assertion.kind == "CATEGORY":
                found.add((assertion.category, assertion.upstream_reason, fragment.source_id))

    if not found:
        return CategoryResult(None, None, (), "NO_RULE")
    if len({(category, reason) for category, reason, _ in found}) > 1:
        return CategoryResult(None, None, (), "CONFLICT")

    category, reason, _ = next(iter(found))
    refs = tuple(sorted({source for _, _, source in found}))
    return CategoryResult(category, reason, refs, "FOUND")


def build_analysis_item(
    item_id: str,
    original: dict[str, object],
    reference: list[MedicineReference],
    diagnosis: Optional[str] = None,
    corpus: Optional[Corpus] = None,
) -> AnalysisItem:
    """Создаёт AnalysisItem: МНН из справочника и категория из базы."""

    match = match_original(original, reference)
    category = None
    classification_reason = None
    source_refs = list(match["source_refs"])
    missing_data = list(match["missing_data"])
    upstream_status = match["upstream_status"]

    if match["inn"] is not None and diagnosis is not None and corpus is not None:
        result = classify_category(match["inn"], diagnosis, corpus)
        if result.status == "FOUND":
            category = result.category
            classification_reason = result.reason
            source_refs = list(result.source_refs)
        else:
            # Основания нет или оно противоречиво: ничего не придумываем.
            upstream_status = "PARTIAL"
            missing_data.append("category" if result.status == "NO_RULE" else "category_conflict")

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
        source_refs=source_refs,
        upstream_status=upstream_status,
        missing_data=missing_data,
    )
