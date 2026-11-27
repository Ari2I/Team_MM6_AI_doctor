"""Встраиваемый фасад: явные пары, локальные ресурсы и ограниченный бюджет."""

from dataclasses import dataclass
from decimal import Decimal
import re
from copy import deepcopy
from hashlib import sha256
import json
from time import monotonic
from typing import Callable

from llm_rag.contracts import RetrievalInfo
from llm_rag.corpus import Corpus, normalize
from llm_rag.evidence_snapshot import EvidenceSnapshot, verify_ml2_evidence
from llm_rag.explanation import explain_pair
from llm_rag.explanation_contracts import ExplanationInput, EvidenceCitation, pair_key
from llm_rag.integration_report import build_integration_human_report as _public_report
from llm_rag.integration_contracts import (
    IntegrationRequest,
    IntegrationResult,
    IntegrationPair,
    UnprocessedPair,
    IntegrationManifest,
    ResourceVersion,
    IntegrationUsage,
)
from llm_rag.integration_policy import category_decision
from llm_rag.ml2.pipeline import ML2Pipeline
from llm_rag.ml2.classifier import EvidenceFragment
from llm_rag.ml2.evidence_selection import EvidenceContextLimit
from llm_rag.ml2.registry import RegistrySnapshot, inn_key
from llm_rag.ml2.form_matching import match_form
from llm_rag.staff_report import recommend_for_staff


@dataclass(frozen=True)
class IntegrationResources:
    registry: RegistrySnapshot | None = None
    evidence: EvidenceSnapshot | None = None
    approved_corpus: Corpus | None = None
    clock: Callable = monotonic
    provider_factory: Callable | None = None
    embedding_factory: Callable | None = None
    source_issues: dict | None = None
    verify_ssl_certs: bool = True


@dataclass(frozen=True)
class IntegrationOptions:
    max_pairs: int = 10000
    deadline_seconds: float = 300.0
    max_calls: int = 100
    max_tokens: int = 500000
    max_context_bytes: int = 100000

    def __post_init__(self):
        from math import isfinite

        if any(
            type(v) is not int or v < 0
            for v in (self.max_pairs, self.max_calls, self.max_tokens, self.max_context_bytes)
        ):
            raise ValueError("Бюджеты должны быть неотрицательными целыми.")
        if (
            isinstance(self.deadline_seconds, bool)
            or not isfinite(self.deadline_seconds)
            or self.deadline_seconds <= 0
        ):
            raise ValueError("Срок обработки должен быть конечным положительным числом.")


def _age_requires_review(quote: str, years: Decimal | None) -> bool:
    """Отметить числовой возраст для сверки или проверить известные границы."""
    if years is None:
        return bool(re.search(
            r"\b\d+(?:[.,]\d+)?\s*(?:лет\b|год(?:а|ов)?\b|месяц(?:а|ев)?\b|[-–]летн\w+)",
            quote, re.IGNORECASE,
        ))
    for low, high in re.findall(r"(\d{1,3})\s*[-–—]\s*(\d{1,3})\s*(?:лет|год)", quote):
        if not int(low) <= years <= int(high):
            return True
    for low, high in re.findall(r"от\s+(\d{1,3})\s+до\s+(\d{1,3})\s*(?:лет|год)", quote, re.IGNORECASE):
        if not int(low) <= years <= int(high):
            return True
    for limit in re.findall(r"(?:до|младше)\s+(\d{1,3})\s*(?:лет|год)", quote, re.IGNORECASE):
        if years >= int(limit):
            return True
    for limit in re.findall(r"старше\s+(\d{1,3})\s*(?:лет|год)", quote, re.IGNORECASE):
        if years <= int(limit):
            return True
    for limit in re.findall(r"\b(?:от|с)\s+(\d{1,3})\s*(?:лет|год)", quote, re.IGNORECASE):
        if years < int(limit):
            return True
    for limit, direction in re.findall(
        r"\b(\d{1,3})\s*(?:лет|год(?:а|ов)?)\s+и\s+(старше|младше)\b", quote, re.IGNORECASE,
    ):
        boundary = int(limit)
        if (direction.casefold() == "старше" and years < boundary
                or direction.casefold() == "младше" and years > boundary):
            return True
    return False


def _descriptor(value, version):
    return (
        ResourceVersion(version=version, sha256=sha256(value.model_dump_json().encode()).hexdigest())
        if value is not None
        else None
    )


def analyze_request(request: IntegrationRequest, *, verify_ssl_certs=True, options=None) -> IntegrationResult:
    """Проверить заявку по ГРЛС и рубрикатору Минздрава, подготовить рекомендации сотруднику."""
    from llm_rag.runtime import prepare_resources

    request = IntegrationRequest.model_validate(request.model_dump())
    options = options or IntegrationOptions()
    start = monotonic()
    resources = prepare_resources(request, verify_ssl_certs, options, start)
    return _run(request, resources=resources, options=options, start=start)


def _run(
    request: IntegrationRequest,
    *,
    resources: IntegrationResources,
    options: IntegrationOptions | None = None,
    start=None,
) -> IntegrationResult:
    """Открыть и закрыть клиент в границах одной заявки."""
    options = options or IntegrationOptions()
    request = IntegrationRequest.model_validate(request.model_dump())
    if resources.provider_factory is None:
        return _analyze_request(request, resources=resources, options=options, start=start)
    from contextlib import ExitStack

    start = resources.clock() if start is None else start
    stack = ExitStack()
    result = None
    entered = False
    try:
        try:
            if resources.provider_factory is None:
                raise ValueError("Не задана фабрика сетевого клиента.")
            provider = stack.enter_context(resources.provider_factory())
            entered = True
            if (
                getattr(provider, "scope", None) not in {"GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}
                or getattr(provider, "verify_ssl_certs", None) is not resources.verify_ssl_certs
            ):
                raise ValueError("Фасад требует корпоративный scope и проверку TLS.")
        except Exception:
            result = _analyze_request(
                request, resources=resources, options=options, initialization_error=True, start=start
            )
        else:
            result = _analyze_request(
                request, resources=resources, options=options, provider=provider, start=start
            )
    finally:
        try:
            stack.close()
        except Exception:
            if result is not None:
                result.lifecycle_status = "CLOSE_FAILED"
        else:
            if result is not None and entered:
                result.lifecycle_status = "CLOSED"
    return result


def _analyze_request(
    request: IntegrationRequest,
    *,
    resources: IntegrationResources,
    options: IntegrationOptions,
    provider=None,
    initialization_error=False,
    start=None,
) -> IntegrationResult:
    """Обработать только явные пары; созданный фабрикой клиент принадлежит текущей заявке."""
    from llm_rag.integration_policy import purchase_totals

    request = IntegrationRequest.model_validate(request.model_dump())
    options = options or IntegrationOptions()
    start = resources.clock() if start is None else start
    from llm_rag.integration_budget import RequestBudget

    budget = RequestBudget(
        start + options.deadline_seconds, options.max_calls, options.max_tokens, resources.clock
    )
    snapshot = (
        EvidenceSnapshot.model_validate(resources.evidence.model_dump())
        if resources.evidence
        else EvidenceSnapshot(snapshot_version="empty")
    )
    approved = (
        Corpus.model_validate(resources.approved_corpus.model_dump()) if resources.approved_corpus else None
    )
    from llm_rag.ml2.classifier import policy_fingerprint

    policy_version, policy_sha = policy_fingerprint()
    manifest = IntegrationManifest(
        registry=_descriptor(resources.registry, resources.registry.version) if resources.registry else None,
        clinical=_descriptor(snapshot, snapshot.snapshot_version),
        corpus=_descriptor(approved, approved.corpus_version) if approved else None,
        policy=ResourceVersion(version=policy_version, sha256=policy_sha),
    )
    mismatch = any(
        getattr(request.versions, key) is not None
        and (
            getattr(manifest, key) is None or getattr(manifest, key).version != getattr(request.versions, key)
        )
        for key in ("registry", "clinical", "corpus", "policy")
    )
    complete = None
    last_failure = {"reason": None}
    classification_cache = {}
    staff_cache = {}
    cache_stats = {"hits": 0}
    active_context = {}
    if provider is not None:
        from llm_rag.ml2.classifier import gigachat_completer

        invoke = gigachat_completer(provider)

        def complete(system, user):
            from llm_rag.network_policy import prepare_classification

            try:
                safe = prepare_classification(json.loads(user), provider.network_policy)
            except Exception as error:
                last_failure["reason"] = (
                    "CONTEXT_LIMIT"
                    if error.args == ("NETWORK_CONTEXT_TOO_LARGE",)
                    else "NETWORK_POLICY_DENIED"
                )
                raise
            user = json.dumps(safe, ensure_ascii=False)
            if len(user.encode()) > options.max_context_bytes:
                last_failure["reason"] = "CONTEXT_LIMIT"
                raise ValueError("Контекст превышает предел заявки.")
            cache_key = sha256(
                (system + user + json.dumps(active_context, sort_keys=True)).encode()
            ).hexdigest()
            if cache_key in classification_cache:
                cache_stats["hits"] += 1
                return deepcopy(classification_cache[cache_key])
            # Верхняя оценка учитывает текст, JSON schema, инструкции и выход.
            reserved = 3 * len(user.encode()) + len(system.encode()) + 4096
            remaining = budget.reserve(reserved)
            with provider.request_limits(timeout=remaining, max_tokens=2048):
                response = invoke(system, user)
            classification_cache[cache_key] = deepcopy(response)
            return response

    pipeline = (
        ML2Pipeline(resources.registry, approved_corpus=approved, complete_json=complete)
        if resources.registry and not mismatch
        else None
    )
    diagnoses = {d.diagnosis_id: d for d in request.diagnoses}
    purchases = {p.item_id: p for p in request.purchases}
    pairs, pending, inputs, explanations = [], [], [], []
    embedder = None
    embedding_error = False
    if resources.embedding_factory is not None:
        try:
            embedder = resources.embedding_factory(before_call=budget.reserve)
        except Exception:
            embedding_error = True
    for position, link in enumerate(request.links):
        identity = dict(request_id=request.request_id, item_id=link.item_id, diagnosis_id=link.diagnosis_id)
        if (
            mismatch
            or budget.exhausted
            or position >= options.max_pairs
            or resources.clock() - start >= options.deadline_seconds
        ):
            reason = "VERSION_MISMATCH" if mismatch and position < options.max_pairs else "BUDGET_EXHAUSTED"
            pending.append(UnprocessedPair(**identity, reason=reason))
            continue
        diagnosis, purchase = diagnoses[link.diagnosis_id], purchases[link.item_id]
        last_failure["reason"] = None
        try:
            if pipeline is None:
                raise OSError("Нет снимка реестра")
            resolved = pipeline.index.resolve(purchase.name, purchase.dosage or "")
            form = match_form(purchase.dosage, [r.form for r in resolved.registrations])
            issue = (resources.source_issues or {}).get((purchase.item_id, diagnosis.diagnosis_id))
            if issue in {"DIAGNOSIS_MISMATCH", "NEEDS_CONTEXT", "NEEDS_REVIEW"}:
                # Применимость редакции проверяется до вызова модели и правил корпуса.
                pairs.append(
                    IntegrationPair(
                        **identity,
                        decision=category_decision(None, None),
                        evidence_status="SOURCE_CONFLICT" if issue == "DIAGNOSIS_MISMATCH" else "INSUFFICIENT_DATA",
                        reasons=[issue, "FORM_MISMATCH"] if form.status == "MISMATCH" else [issue],
                        inn=resolved.mnn,
                        form_status=form.status,
                        form_reason=form.reason,
                    )
                )
                continue
            if embedding_error:
                raise ValueError("Векторный поиск недоступен.")
            context = {}
            if diagnosis.recommendation_edition:
                context["recommendation_edition"] = diagnosis.recommendation_edition
            if diagnosis.icd10:
                context["icd10"] = diagnosis.icd10
            if (age := diagnosis.conditions.age_years) is not None:
                context["age"] = str(age)
            if (population := diagnosis.conditions.recommendation_population) is not None:
                context["age_group"] = population
            if diagnosis.conditions.pregnant is not None:
                context["pregnant"] = str(diagnosis.conditions.pregnant).lower()

            if issue == "SOURCE_ERROR":
                raise OSError("Не удалось проверить КР.")
            matching_documents = [
                d
                for d in snapshot.documents
                if not issue
                and normalize(d.diagnosis) == normalize(diagnosis.name)
                and (not d.required_context.get("icd10") or d.required_context["icd10"] == diagnosis.icd10)
                and d.required_context.get("recommendation_edition") == diagnosis.recommendation_edition
                and resolved.mnn
                and inn_key(d.inn) == inn_key(resolved.mnn)
                and (
                    "age_group" not in context
                    or d.required_context.get("age_group", context["age_group"]) == context["age_group"]
                )
            ]
            fragments = tuple(
                EvidenceFragment(
                    d.fragment_id, d.source_id, d.title, d.version, d.url, d.location, d.text, d.copy_url
                )
                for d in matching_documents
            )
            similarities = {}
            if embedder is not None and fragments:
                from llm_rag.vector_index import VectorIndex

                canonical = matching_documents[0]
                texts = [f"МНН: {canonical.inn}. Диагноз: {canonical.diagnosis}. {f.text}" for f in fragments]

                def fragment_key(f):
                    return json.dumps([f.source_id, f.fragment_id, f.version], ensure_ascii=False)

                index = VectorIndex([fragment_key(f) for f in fragments], embedder.embed_documents(texts))
                scores = index.similarities(
                    embedder.embed_query(f"МНН: {canonical.inn}. Диагноз: {canonical.diagnosis}.")
                )
                similarities = {
                    (f.source_id, f.version, f.fragment_id): scores[fragment_key(f)]
                    for f in fragments
                }
                fragments = tuple(
                    sorted(fragments, key=lambda f: (-scores[fragment_key(f)], fragment_key(f)))
                )
            active_context = dict(context)
            def checkpoint():
                if resources.clock() >= budget.deadline:
                    budget.exhausted = True
                    from llm_rag.integration_budget import BudgetExhausted
                    raise BudgetExhausted("Бюджет заявки исчерпан.")

            evaluation = pipeline.evaluate_pair(
                request_id=request.request_id,
                item_id=purchase.item_id,
                diagnosis=diagnosis.name,
                trade_name=purchase.name,
                dosage=purchase.dosage,
                context=context,
                fragments=fragments,
                evidence_limits={
                    "max_context_bytes": options.max_context_bytes, "similarities": similarities,
                    "checkpoint": checkpoint,
                } if provider is not None and approved is None else None,
            )
            if budget.exhausted:
                pending.append(UnprocessedPair(**identity, reason="BUDGET_EXHAUSTED"))
                continue
            if last_failure["reason"] == "CONTEXT_LIMIT":
                pending.append(UnprocessedPair(**identity, reason="CONTEXT_LIMIT"))
                continue
            result = evaluation.classification
            upstream = result.proposed_category if evaluation.rule_applied else None
            proposal = None if evaluation.rule_applied else result.proposed_category
            inp = ExplanationInput(
                **identity,
                diagnosis=diagnosis.name,
                inn=result.inn,
                category=result.proposed_category,
                ml2_status=result.status.value,
                rule_applied=evaluation.rule_applied,
                classification_reason=result.review_reason,
                conditions=list(result.conditions),
                missing_context=list(result.missing_context),
                citations=[EvidenceCitation(**vars(c)) for c in result.citations],
                context=context,
                dosage_check=evaluation.dosage_check,
            )
            if inp.citations:
                unapproved = not evaluation.rule_applied
                if unapproved:
                    # Сверка текста цитаты не устанавливает её клиническую применимость.
                    inp.missing_context = sorted({*inp.missing_context, "clinical_applicability"})
                if age is not None:
                    if (unapproved or diagnosis.conditions.age_group is not None) and any(
                        _age_requires_review(c.quote, age) for c in inp.citations
                    ):
                        inp.missing_context.append("citation_age_applicability")
                else:
                    has_group = "age_group" in context
                    age_hint = r"возраст|\bлет\b" if has_group else r"возраст|\bлет\b|детям|детей|взросл"
                    # Даже согласованная категория оставляет числовое условие на сверке.
                    if (
                        has_group and any(_age_requires_review(c.quote, None) for c in inp.citations)
                        or unapproved and any(re.search(age_hint, c.quote, re.I) for c in inp.citations)
                    ):
                        inp.missing_context.append("context.age")
            evidence = verify_ml2_evidence(inp, snapshot, approved_corpus=approved)
            explanation = explain_pair(inp, evidence)
            reasons = {
                "INN_UNRESOLVED": "INN_UNRESOLVED",
                "NO_EVIDENCE": "NO_EVIDENCE",
                "NEEDS_CONTEXT": "NEEDS_CONTEXT",
                "NEEDS_REVIEW": "NEEDS_REVIEW" if provider is not None else "MODEL_DISABLED",
                "PROVIDER_ERROR": "AI_UNAVAILABLE",
                "INVALID_RESPONSE": "AI_INVALID_RESPONSE",
                "SOURCE_ERROR": "SOURCE_ERROR",
                "CONFLICT": "CATEGORY_CONFLICT",
            }.get(result.status.value)
            reasons = last_failure["reason"] or issue or reasons
            pair = IntegrationPair(
                **identity,
                decision=category_decision(upstream, proposal),
                inn=result.inn,
                policy_version=result.policy_version,
                evidence_status=explanation.evidence_status,
                reasons=[reasons] if reasons else [],
                conditions=list(result.conditions),
                citations=explanation.citations,
                explanation=explanation,
                form_status=form.status,
                form_reason=form.reason,
                classification_diagnostics=evaluation.classification_diagnostics,
            )
            if form.status == "MISMATCH":
                pair.reasons.append("FORM_MISMATCH")
            inputs.append(inp)
            explanations.append(explanation)
        except EvidenceContextLimit:
            pending.append(UnprocessedPair(**identity, reason="CONTEXT_LIMIT"))
            continue
        except Exception:
            if budget.exhausted:
                pending.append(UnprocessedPair(**identity, reason="BUDGET_EXHAUSTED"))
                continue
            # Не перехватываем SystemExit/KeyboardInterrupt; технические подробности не публикуем.
            pair = IntegrationPair(
                **identity,
                decision=category_decision(None, None),
                evidence_status="PROCESSING_ERROR",
                reasons=["SOURCE_ERROR"],
                generation_mode="FALLBACK",
            )
        pairs.append(pair)
        if initialization_error:
            pair.reasons.append("AI_UNAVAILABLE")
            pair.generation_mode = "FALLBACK"
    staff_records = {}
    if inputs:

        class BudgetedStaff:
            def generate(self, payload):
                import json
                from llm_rag.network_policy import prepare_payload

                try:
                    safe, restore = prepare_payload(payload, provider.network_policy)
                except Exception as error:
                    last_failure["reason"] = (
                        "CONTEXT_LIMIT"
                        if error.args == ("NETWORK_CONTEXT_TOO_LARGE",)
                        else "NETWORK_POLICY_DENIED"
                    )
                    raise
                if len(json.dumps(safe, ensure_ascii=False).encode()) > options.max_context_bytes:
                    last_failure["reason"] = "CONTEXT_LIMIT"
                    raise ValueError("Контекст рекомендаций превышает предел заявки.")
                cache_key = sha256(
                    (
                        json.dumps(safe, ensure_ascii=False, sort_keys=True)
                        + json.dumps(inp.context, sort_keys=True)
                    ).encode()
                ).hexdigest()
                if cache_key in staff_cache:
                    cache_stats["hits"] += 1
                    return restore(deepcopy(staff_cache[cache_key]))
                remaining = budget.reserve(3 * len(json.dumps(safe, ensure_ascii=False).encode()) + 8192)
                with provider.request_limits(timeout=remaining, max_tokens=1500):
                    response = provider.generate(safe)
                staff_cache[cache_key] = deepcopy(response)
                return restore(response)

        for inp, explanation in zip(inputs, explanations):
            last_failure["reason"] = None
            staff = recommend_for_staff([inp], [explanation], provider=BudgetedStaff() if provider else None)
            staff_records[pair_key(inp)] = (
                staff.recommendations[0],
                staff.generation_status,
                last_failure["reason"],
            )
    costs = purchase_totals(request)
    for pair in pairs:
        if pair_key(pair) in staff_records:
            rec, status, failure = staff_records[pair_key(pair)]
            pair.staff_actions = rec.actions
            pair.staff_analysis = rec.analysis
            if provider:
                pair.generation_mode = status
                if status == "FALLBACK":
                    pair.reasons.append(
                        failure or ("BUDGET_EXHAUSTED" if budget.exhausted else "AI_INVALID_RESPONSE")
                    )
    report = _public_report(request, pairs, pending, costs)
    return IntegrationResult(
        request=request,
        pairs=pairs,
        unprocessed_pairs=pending,
        completeness="PARTIAL"
        if pending or budget.exhausted or any("CONTEXT_LIMIT" in pair.reasons for pair in pairs)
        else "COMPLETE",
        manifest=manifest,
        evidence_snapshot=snapshot,
        costs=costs,
        usage=IntegrationUsage(
            calls=budget.calls,
            cache_hits=cache_stats["hits"],
            tokens=budget.tokens,
            token_accounting="UPPER_BOUND" if budget.calls else "ACTUAL",
            elapsed_seconds=float(resources.clock() - start),
        ),
        human_report=report,
        retrieval_info=RetrievalInfo(
            method="vector" if resources.embedding_factory else "lexical",
            embedding_model=embedder.model_id if embedder else None,
            embedding_revision=embedder.revision if embedder else None,
            embedding_dimension=embedder.dimension if embedder else None,
            corpus_fingerprint=manifest.clinical.sha256,
        ),
    )
