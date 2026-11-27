"""ML-2: JSON-заявка -> контракт AnalysisRequest для существующего RAG."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from .official import ClinicalSources, SelectionError, select_edition

import httpx

from llm_rag.contracts import AnalysisItem, AnalysisRequest, DosageCheck
from llm_rag.corpus import Corpus

from .classifier import (
    Citation,
    ClassificationRequest,
    ClassificationResult,
    ClassificationStatus,
    EvidenceFragment,
    classify_pair,
)
from .clinicalrecs import (
    CatalogCandidate,
    fragments_for_inn,
)
from .registry import RegistryIndex, RegistrySnapshot, canonical_code, inn_key, name_key


@dataclass(frozen=True)
class ItemEvaluation:
    classification: ClassificationResult
    dosage_check: DosageCheck
    rule_applied: bool = False
    candidates: tuple[CatalogCandidate, ...] = ()
    recommendation: Mapping[str, str] | None = None
    classification_diagnostics: dict | None = None


@dataclass(frozen=True)
class ML2Batch:
    request: AnalysisRequest
    evaluations: tuple[ItemEvaluation, ...]
    registry_version: str

    def details(self) -> dict:
        return {
            "schema_version": "ml2.1",
            "request_id": self.request.request_id,
            "registry_version": self.registry_version,
            "items": [
                asdict(value)
                | {"dosage_check": value.dosage_check.model_dump(mode="json")}
                for value in self.evaluations
            ],
        }


def _code(request: AnalysisRequest, explicit: str | None) -> str | None:
    match = re.match(
        r"^\s*([A-ZА-ЯЁ][0-9]{2}(?:\.[0-9]{1,4})?)(?=\s|:|$)",
        request.diagnosis,
        re.IGNORECASE,
    )
    value = explicit or request.context.get("icd10") or (match[1] if match else "")
    code = canonical_code(value)
    return code if re.fullmatch(r"[A-Z][0-9]{2}(?:\.[0-9]{1,4})?", code) else None


def _age_group(context: Mapping[str, str]) -> str | None:
    value = name_key(context.get("age_group", ""))
    if value in {"adult", "adults", "взрослый", "взрослые"}:
        return "Взрослые"
    if value in {"child", "children", "ребенок", "дети"}:
        return "Дети"
    age = context.get("age", "")
    if age.isdigit() and 0 <= int(age) <= 130:
        return "Взрослые" if int(age) >= 18 else "Дети"
    return None


class ML2Pipeline:
    """Новые файлы ML-2 не меняют конвейер и контракты коллеги.

    approved_corpus передаётся явно: это согласованные правила, не результаты LLM.
    fragments_by_inn позволяет воспроизводить обработку без сетевого парсинга.
    """

    def __init__(
        self,
        registry: RegistrySnapshot,
        *,
        complete_json: Callable | None = None,
        approved_corpus: Corpus | None = None,
        fragments_by_inn: Mapping[str, tuple[EvidenceFragment, ...]] | None = None,
        sources: ClinicalSources | None = None,
    ):
        self.index = RegistryIndex(registry)
        self.complete = complete_json
        self.approved = (
            Corpus.model_validate(approved_corpus.model_dump())
            if approved_corpus is not None
            else None
        )
        self.fragments = {
            inn_key(k): tuple(v) for k, v in (fragments_by_inn or {}).items()
        }
        self.sources = sources or ClinicalSources()

    def _rule(self, request, item, inn):
        if self.approved is None:
            return None
        sources = {s.source_id: s for s in self.approved.sources}
        matches, absent = [], set()
        for fragment in self.approved.fragments:
            if inn_key(fragment.inn) != inn_key(inn) or name_key(
                fragment.diagnosis
            ) != name_key(request.diagnosis):
                continue
            if any(
                k in request.context and request.context[k] != v
                for k, v in fragment.required_context.items()
            ):
                continue
            missing = set(fragment.required_context) - request.context.keys()
            category_assertions = [
                a for a in fragment.assertions if a.kind == "CATEGORY"
            ]
            if missing and category_assertions:
                absent.update("context." + k for k in missing)
                continue
            matches.extend((fragment, a) for a in category_assertions)
        if absent:
            return self._result(
                request,
                item,
                inn,
                "NEEDS_CONTEXT",
                "Не хватает условий применимости согласованных правил.",
                missing=sorted(absent),
            )
        if not matches:
            return None
        categories = {a.category for _, a in matches}
        citations = tuple(
            Citation(
                fragment_id=f.fragment_id,
                source_id=f.source_id,
                title=sources[f.source_id].title,
                version=sources[f.source_id].version,
                url="",
                location=f.location,
                quote=a.quote,
            )
            for f, a in matches
        )
        if len(categories) != 1:
            return self._result(
                request,
                item,
                inn,
                "CONFLICT",
                "Применимые согласованные правила противоречат друг другу.",
                citations=citations,
            )
        return ClassificationResult(
            request.request_id,
            item.item_id,
            request.diagnosis,
            inn,
            None,
            next(iter(categories)),
            ClassificationStatus.PROPOSED,
            citations=citations,
            review_reason=matches[0][1].upstream_reason,
        )

    def evaluate_pair(self, *, request_id, item_id, diagnosis, trade_name,
                      dosage=None, context=None, fragments=(), evidence_limits=None):
        """Обработать узкую пару без искусственных денег и количества.

        Только переданные фрагменты; сетевые загрузки этим методом запрещены.
        Временные объекты содержат лишь поля, реально используемые ML-2.
        """
        from types import SimpleNamespace
        request = SimpleNamespace(request_id=request_id, diagnosis=diagnosis,
                                  context=dict(context or {}))
        item = SimpleNamespace(item_id=item_id, original=SimpleNamespace(
            trade_name=trade_name, dosage=dosage or ''))
        if evidence_limits is not None and self.complete is not None and fragments:
            from .evidence_evaluation import evaluate_packets

            inn = self.index.resolve(trade_name, dosage or '').mnn
            if inn and '+' not in inn:
                return evaluate_packets(
                    fragments=tuple(fragments), inn=inn, diagnosis=diagnosis,
                    context=dict(context or {}), limits=evidence_limits, complete=self.complete,
                    evaluate=lambda packet, retry: self._evaluate(
                        request, item, _code(request, None), {},
                        fragments_override=packet, complete_override=retry),
                )
        return self._evaluate(request, item, _code(request, None), {},
                              fragments_override=tuple(fragments))

    @staticmethod
    def _result(
        request, item, inn, status, reason, *, missing=(), citations=(), registry=()
    ):
        return ClassificationResult(
            request.request_id,
            item.item_id,
            request.diagnosis,
            inn,
            None,
            None,
            ClassificationStatus(status),
            missing_context=tuple(missing),
            citations=tuple(citations),
            registry_citations=tuple(registry),
            review_reason=reason,
        )

    def _evaluate(self, request, item, code, catalog_cache, *, fragments_override=None, complete_override=None):
        drug = self.index.resolve(item.original.trade_name, item.original.dosage)
        registry = tuple(
            Citation(
                row.source.citation()["fragment_id"],
                row.source.citation()["source_id"],
                row.source.title,
                row.source.version,
                row.source.url,
                row.source.location,
                row.source.quote,
            )
            for row in drug.registrations[:8]
        )
        dosage = DosageCheck(
            status="INSUFFICIENT_DATA",
            reason="Схема приёма не передана; дозировка не подтверждена.",
            missing_data=["разовая доза", "кратность приёма", "длительность курса"],
        )

        def finish(result, *, rule=False, candidates=(), recommendation=None):
            from dataclasses import replace

            result = replace(
                result,
                registry_citations=registry,
                candidate_mnns=tuple(drug.candidates),
            )
            return ItemEvaluation(
                result, dosage, rule, tuple(candidates), recommendation
            )

        if drug.mnn is None:
            return finish(
                self._result(
                    request,
                    item,
                    None,
                    "INN_UNRESOLVED",
                    "МНН не найдено или неоднозначно по снимку ГРЛС.",
                    missing=["inn"],
                )
            )
        inn = drug.mnn
        if "+" in inn:
            return finish(
                self._result(
                    request,
                    item,
                    inn,
                    "MULTI_INGREDIENT_REVIEW",
                    "Комбинированный состав требует отдельного согласования.",
                )
            )
        rule = self._rule(request, item, inn)
        if rule is not None:
            return finish(rule, rule=rule.proposed_category is not None)
        fragments = (self.fragments.get(inn_key(inn), ()) if fragments_override is None
                     else fragments_override)
        candidates, metadata = (), None
        if not fragments and fragments_override is None:
            age = _age_group(request.context)
            key = (code, age)
            if key not in catalog_cache:
                catalog_cache[key] = tuple(self.sources.candidates(code, age)) if code else ()
            candidates = catalog_cache[key]
            try:
                cached = select_edition(self.sources, candidates, code=code, age=age,
                                        edition=request.context.get("recommendation_edition"))
            except SelectionError as error:
                return finish(self._result(request, item, inn, error.status, str(error), missing=error.missing),
                              candidates=candidates)
            rec = cached.recommendation
            metadata = {"edition_id": rec.edition_id, "version": rec.version, "primary_url": rec.primary_url,
                        "page_url": rec.page_url, "cache_state": cached.cache_state}
            fragments = fragments_for_inn(rec, inn)
        if not fragments:
            result = self._result(
                request,
                item,
                inn,
                "NO_EVIDENCE",
                "Основание не найдено в предоставленных фрагментах. Это не категория В.",
            )
        elif self.complete is None:
            result = self._result(
                request,
                item,
                inn,
                "NEEDS_REVIEW",
                "Модель не включена; фрагменты требуют сверки.",
            )
        else:
            original_ids={}
            if len({f.fragment_id for f in fragments})!=len(fragments):
                renamed=[]
                for index,fragment in enumerate(fragments):
                    alias=f'source-fragment-{index}'
                    original_ids[(fragment.source_id,alias,fragment.version)]=fragment.fragment_id
                    renamed.append(replace(fragment,fragment_id=alias))
                fragments=tuple(renamed)
            context = {
                k: v
                for k, v in request.context.items()
                if k != "Подтверждённая категория по правилу"
            }
            result = classify_pair(
                ClassificationRequest(
                    request.request_id,
                    item.item_id,
                    request.diagnosis,
                    inn,
                    tuple(fragments),
                    context,
                ),
                complete_override or self.complete,
            )
            if original_ids:
                result=replace(result,citations=tuple(replace(c,fragment_id=original_ids.get(
                    (c.source_id,c.fragment_id,c.version),c.fragment_id)) for c in result.citations))
        return finish(result, candidates=candidates, recommendation=metadata)

    def run(
        self,
        request: AnalysisRequest,
        *,
        diagnosis_code: str | None = None,
    ) -> ML2Batch:
        request = AnalysisRequest.model_validate(request.model_dump())
        code = _code(request, diagnosis_code)
        evaluations, items, catalog_cache = [], [], {}
        for item in request.items:
            try:
                evaluation = self._evaluate(
                    request, item, code, catalog_cache
                )
            except (OSError, ValueError, httpx.HTTPError):
                # Не выводим сырые сетевые исключения и не ломаем остальные строки.
                inn = self.index.resolve(
                    item.original.trade_name, item.original.dosage
                ).mnn
                evaluation = ItemEvaluation(
                    self._result(
                        request,
                        item,
                        inn,
                        "SOURCE_ERROR",
                        "Ошибка чтения или проверки источника; нужна ручная сверка.",
                    ),
                    DosageCheck(
                        status="INSUFFICIENT_DATA",
                        reason="Дозировка не проверена.",
                        missing_data=["схема приёма"],
                    ),
                )
            evaluations.append(evaluation)
            value = evaluation.classification
            items.append(
                AnalysisItem(
                    item_id=item.item_id,
                    original=item.original.model_copy(deep=True),
                    inn=value.inn,
                    category=value.proposed_category,
                    classification_reason=value.review_reason,
                    dosage_check=evaluation.dosage_check,
                    source_refs=list(
                        dict.fromkeys(c.fragment_id for c in value.citations)
                    ),
                    upstream_status="PROCESSED"
                    if value.proposed_category
                    else "PARTIAL",
                    missing_data=list(value.missing_context),
                )
            )
        prepared = AnalysisRequest(
            schema_version="1.0",
            request_id=request.request_id,
            diagnosis=request.diagnosis,
            context=dict(request.context),
            items=items,
        )
        return ML2Batch(prepared, tuple(evaluations), self.index.snapshot.version)
