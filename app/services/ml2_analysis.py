"""Backend bridge to the installed ML-2, real GRLS and cached/live clinical sources."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.config import MODEL_ERROR_LABELS, AINetworkSettings, AISettings
from app.models.enums import ReviewReason
from app.schemas.analysis import PairCheck
from app.services.ai_adapter import AIInput, WorkerResult


def load_pair_contracts() -> tuple[type[Any], type[Any], type[Any], type[Any]]:
    """Импортировать старый AI-контракт только по явному вызову offline/network."""
    from llm_rag.contracts import AnalysisItem, AnalysisRequest, Original
    from llm_rag.explanation_contracts import ExplanationInput
    from pydantic import Field

    class PairOriginal(Original):
        """Purchase fields are optional and never invented for a clinical pair."""

        dosage: str | None
        quantity: Decimal | None
        amount: Decimal | None
        currency: str | None

    class PairItem(AnalysisItem):
        original: PairOriginal

    class PairRequest(AnalysisRequest):
        items: list[PairItem] = Field(min_length=1)
        is_demo: bool = Field(default=False, strict=True)

    class PairExplanationInput(ExplanationInput):
        is_demo: bool = Field(default=False, strict=True)

    return PairOriginal, PairItem, PairRequest, PairExplanationInput


def create_sources(cache: Path) -> Any:
    """Лениво создать источник; его сетевой доступ ограничивает worker."""
    from llm_rag.evidence_snapshot import EvidenceDocument, EvidenceSnapshot
    from llm_rag.ml2.clinicalrecs import fragments_for_inn
    from llm_rag.ml2.pipeline import ClinicalSources

    class CapturedSources(ClinicalSources):
        """Keep the parsed public text for independent verification after ML-2."""

        def __init__(self, cache: Path) -> None:
            super().__init__(cache=cache)
            self.loaded: list[Any] = []
            self.fragment_count = 0

        def load(self, candidate: Any) -> Any:
            cached = super().load(candidate)
            self.loaded.append(cached)
            return cached

        def snapshot(self, pair: AIInput, mnn: str | None) -> Any:
            documents: dict[tuple[str, str, str], EvidenceDocument] = {}
            for cached in self.loaded:
                rec = cached.recommendation
                for fragment in fragments_for_inn(rec, mnn or ""):
                    document = EvidenceDocument(
                        source_id=fragment.source_id,
                        fragment_id=fragment.fragment_id,
                        title=fragment.title,
                        version=fragment.version,
                        url=fragment.url,
                        copy_url=fragment.copy_url,
                        location=fragment.location,
                        text=fragment.text,
                        diagnosis=pair.diagnosis,
                        inn=mnn,
                        retrieved_at=rec.retrieved_at,
                    )
                    documents[(fragment.source_id, fragment.fragment_id, fragment.version)] = (
                        document
                    )
            self.fragment_count = len(documents)
            digest = sha256(
                "".join(d.model_dump_json() for d in documents.values()).encode()
            ).hexdigest()
            return EvidenceSnapshot(
                snapshot_version="clinical-" + digest[:16], documents=list(documents.values())
            )

    return CapturedSources(cache)


def create_provider(timeout: float) -> Any:
    """Reuse the same SDK, scope, credentials and TLS settings as the existing adapter."""
    credentials = AINetworkSettings()
    from llm_rag.ml2.provider import ML2GigaChatProvider

    return ML2GigaChatProvider(
        credentials=credentials.credentials.get_secret_value(),
        enabled=True,
        verify_ssl_certs=True,
        ca_bundle=credentials.ca_bundle_file or None,
        scope=credentials.scope or "GIGACHAT_API_PERS",
        timeout=timeout,
    )


def _field_names(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        return [
            name
            for key, child in value.items()
            for name in _field_names(child, prefix + str(key) + ".")
        ]
    if isinstance(value, list):
        return sorted(
            {name for child in value for name in _field_names(child, prefix.rstrip(".") + "[].")}
        )
    return [prefix.rstrip(".")]


def analyze_pair(
    pair: AIInput,
    local: PairCheck,
    settings: AISettings,
    timeout: float,
    progress: Callable[..., None] | None = None,
) -> WorkerResult:
    if settings.mode == "template":
        return WorkerResult(check=local.model_dump(mode="json"))

    from llm_rag.evidence_snapshot import verify_ml2_evidence
    from llm_rag.explanation import explain_pair
    from llm_rag.ml2.classifier import gigachat_completer
    from llm_rag.ml2.pipeline import ML2Pipeline
    from llm_rag.ml2.registry import RegistrySnapshot

    PairOriginal, PairItem, PairRequest, PairExplanationInput = load_pair_contracts()
    diagnostics: dict[str, Any] = {"pipeline": "ml2", "live_sources": settings.live_sources}

    def failure(message: str) -> WorkerResult:
        check = local.model_copy(
            update={
                "needs_review": True,
                "review_reason": ReviewReason.AI_UNAVAILABLE,
                "source": local.source or "ГРЛС и клинические рекомендации",
                "reference_version": local.reference_version or "unconfigured",
                "comment": message + " Требуется ручная проверка.",
            }
        )
        return WorkerResult(check=check.model_dump(mode="json"), diagnostics=diagnostics)

    registry_path = Path(settings.registry_path)
    if not settings.registry_path or not registry_path.is_file():
        diagnostics["data_error"] = "REGISTRY_NOT_FOUND"
        return failure(
            "Справочник ГРЛС не найден. Обратитесь к администратору для его подключения."
        )
    try:
        registry = RegistrySnapshot.load(registry_path)
    except (OSError, ValueError):
        diagnostics["data_error"] = "REGISTRY_INVALID"
        return failure("Справочник ГРЛС не читается или имеет неверный формат.")
    diagnostics["registry_entries"] = len(registry.registrations)
    if not settings.clinical_cache_dir:
        diagnostics["data_error"] = "CACHE_NOT_CONFIGURED"
        return failure(
            "Хранилище клинических рекомендаций не подключено. Обратитесь к администратору."
        )
    cache = Path(settings.clinical_cache_dir)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=cache, prefix=".write-check-", delete=True):
            pass
    except OSError:
        diagnostics["data_error"] = "CACHE_NOT_WRITABLE"
        return failure("Каталог кэша клинических рекомендаций недоступен для записи.")

    context = {"age_group": pair.age_group} if pair.age_group else {}
    request = PairRequest(
        schema_version="1.0",
        request_id="pair",
        diagnosis=pair.diagnosis,
        context=context,
        items=[
            PairItem(
                item_id="item",
                original=PairOriginal(
                    trade_name=pair.drug_name,
                    dosage=pair.dosage,
                    quantity=pair.quantity,
                    amount=pair.amount,
                    currency=None,
                ),
                inn=None,
                category=None,
                classification_reason=None,
                dosage_check={"status": "NOT_CHECKED", "reason": None, "missing_data": []},
                source_refs=[],
                upstream_status="PARTIAL",
                missing_data=[],
            )
        ],
    )
    sources = create_sources(cache)
    provider: Any = None
    fields: set[str] = set()
    tokens = 0
    diagnostics.update(model_calls=0, model_responses=0, rendering_calls=0)
    model_errors = {
        "GIGACHAT_TIMEOUT",
        "GIGACHAT_TLS_ERROR",
        "GIGACHAT_UNAVAILABLE",
        "GIGACHAT_AUTH_FAILED",
        "GIGACHAT_FORBIDDEN",
        "GIGACHAT_RATE_LIMIT",
        "GIGACHAT_BAD_REQUEST",
        "GIGACHAT_SERVER_ERROR",
        "GIGACHAT_API_ERROR",
        "GIGACHAT_INVALID_JSON",
        "GIGACHAT_INVALID_RESPONSE",
        "GIGACHAT_NOT_FOUND",
        "GIGACHAT_INVALID_COMPLETION",
        "GIGACHAT_INCOMPLETE_COMPLETION",
    }

    def complete(system: str, user: str) -> Any:
        nonlocal provider, tokens
        try:
            if provider is None:
                provider = create_provider(timeout)
            fields.update(_field_names(json.loads(user)))
            diagnostics["model_calls"] += 1
            if progress:
                progress(sent_fields=sorted(fields), tokens=tokens)
            result = gigachat_completer(provider)(system, user)
            diagnostics["model_responses"] += 1
            return result
        except Exception as error:
            code = error.args[0] if len(error.args) == 1 else ""
            diagnostics["model_error"] = (
                "MODEL_SDK_MISSING"
                if isinstance(error, ImportError)
                else "MODEL_NOT_CONFIGURED"
                if provider is None
                else code
                if isinstance(code, str) and code in model_errors
                else "MODEL_UNAVAILABLE"
            )
            raise RuntimeError("MODEL_UNAVAILABLE") from None
        finally:
            tokens += getattr(provider, "last_total_tokens", None) or 0
            if progress:
                progress(sent_fields=sorted(fields), tokens=tokens)

    try:
        batch = ML2Pipeline(
            registry,
            sources=sources,
            complete_json=complete if settings.mode == "network" else None,
        ).run(request, live_sources=settings.live_sources)
        evaluation = batch.evaluations[0]
        result = evaluation.classification
        snapshot = sources.snapshot(pair, result.inn)
        diagnostics.update(
            ml2_status=result.status.value,
            candidate_count=len(evaluation.candidates),
            fragment_count=sources.fragment_count,
            cache_states=[value.cache_state for value in sources.loaded],
            evidence_citations=len(result.citations),
        )
        input_value = PairExplanationInput(
            request_id="pair",
            item_id="item",
            diagnosis_id="diagnosis",
            diagnosis=pair.diagnosis,
            inn=result.inn,
            category=result.proposed_category,
            ml2_status=result.status.value,
            classification_reason=result.review_reason,
            conditions=list(result.conditions),
            missing_context=list(result.missing_context),
            citations=[vars(citation) for citation in result.citations],
            source_refs=[citation.fragment_id for citation in result.citations],
            context=context,
            dosage_check={"status": "NOT_CHECKED", "reason": None, "missing_data": []},
        )
        evidence = verify_ml2_evidence(input_value, snapshot)
        explanation = explain_pair(input_value, evidence, check_dosage=False)
        diagnostics["evidence_status"] = evidence.status
        comment = explanation.comment
        if result.review_reason:
            comment += " " + result.review_reason
        if not settings.live_sources:
            comment += " Поиск клинических рекомендаций отключён в настройках."
        if result.status.value == "SOURCE_ERROR":
            comment += " Источник клинических рекомендаций недоступен; обратитесь к администратору."
        if diagnostics.get("model_error"):
            comment += " " + MODEL_ERROR_LABELS.get(
                diagnostics["model_error"], "ИИ недоступен. Требуется проверка специалистом."
            )
        for citation in evidence.citations:
            comment += f" Цитата: «{citation.quote}» ({citation.title}; {citation.location})."

        # Preserve the existing evidence-only generation stage after ML-2.
        if provider is not None and result.status.value == "PROPOSED" and evidence.citations:
            from app.services.ai_worker import AllowlistedProvider

            claims = [
                dict(
                    claim_id=str(i),
                    text="Материал для сверки.",
                    fragment_id=c.fragment_id,
                    quote=c.quote,
                )
                for i, c in enumerate(evidence.citations)
            ]
            payload = dict(
                schema_version="1.0",
                is_demo=False,
                corpus_version=snapshot.snapshot_version,
                item=dict(
                    item_id="item",
                    inn=result.inn,
                    diagnosis=pair.diagnosis,
                    category=result.proposed_category,
                ),
                evidence=[
                    dict(
                        **claim,
                        kind="CATEGORY",
                        source_id=c.source_id,
                        source_title=c.title,
                        source_version=c.version,
                        location=c.location,
                        fragment_text=c.quote,
                    )
                    for claim, c in zip(claims, evidence.citations, strict=True)
                ],
            )
            guarded = AllowlistedProvider(provider, progress)
            diagnostics["rendering_calls"] += 1
            before = provider.usage_totals.get("total_tokens", 0)
            try:
                rendered = guarded.generate(payload)
                if rendered != {"item_id": "item", "claims": claims}:
                    raise ValueError("INVALID_RENDERING")
                diagnostics["rendering_verified"] = True
            except Exception:
                comment += " Оформление моделью не прошло проверку; сохранён шаблон."
                diagnostics["rendering_verified"] = False
            finally:
                fields.update(guarded.sent_fields)
                tokens += provider.usage_totals.get("total_tokens", 0) - before

        references = [f"ГРЛС {registry.version}"]
        references += [
            f"{c.title} ({c.url}; {c.version}; {c.location})" for c in evidence.citations
        ]
        if not evidence.citations and sources.loaded:
            rec = sources.loaded[0].recommendation
            references.append(f"{rec.title} ({rec.primary_url}; {rec.version})")
        version = "grls:" + registry.version
        if sources.loaded:
            version += "; " + sources.loaded[0].recommendation.version
        reason = (
            ReviewReason.AI_PRELIMINARY
            if result.proposed_category
            else ReviewReason.AI_REVIEW_REQUIRED
        )
        if result.status.value == "PROVIDER_ERROR":
            reason = ReviewReason.AI_UNAVAILABLE
        elif result.status.value == "INVALID_RESPONSE":
            reason = ReviewReason.AI_INVALID_RESPONSE
        check = PairCheck(
            category=result.proposed_category,
            source="; ".join(references),
            reference_version=version,
            comment=comment,
            needs_review=True,
            review_reason=reason,
        )
        return WorkerResult(
            check=check.model_dump(mode="json"),
            mnn=result.inn,
            diagnostics=diagnostics,
            tokens=tokens or None,
            sent_fields=sorted(fields),
        )
    finally:
        if provider is not None:
            provider.close()
