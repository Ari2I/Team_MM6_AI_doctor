"""Связь результата backend с точным фрагментом и проверкой LLM/RAG."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from functools import lru_cache
from threading import BoundedSemaphore
from time import monotonic
import os

from llm_rag.classification import classify_category
from llm_rag.contracts import Citation, DosageCheck, PairContext, PairExplanation, PairInput
from llm_rag.corpus import normalize
from llm_rag.embeddings import LocalMultilingualEmbedder
from llm_rag.evidence import EvidenceClaim
from llm_rag.pipeline import Pipeline
from llm_rag.providers import DemoProvider
from llm_rag.providers.gigachat import GigaChatProvider
from llm_rag.providers.base import ProviderError, PROVIDER_ERROR_MESSAGES
from llm_rag.validation import GenerationError, citation_for

from app.models.application import ItemCheck
from app.services.reference_seed import CATEGORIES, ReferenceBundle


@dataclass(frozen=True)
class PairJob:
    context: PairContext
    item: PairInput
    fragment_id: str


@lru_cache(maxsize=2)
def get_pipeline(reference_json: str, retrieval: str) -> Pipeline:
    corpus = ReferenceBundle.model_validate_json(reference_json).corpus
    if retrieval == "vector":
        try:
            return Pipeline(corpus, DemoProvider(), embedder=LocalMultilingualEmbedder())
        except (ValueError, RuntimeError, ImportError, OSError):
            pass
    return Pipeline(corpus, DemoProvider())


def _citations(citations: list[Citation], bundle: ReferenceBundle) -> list[dict]:
    sources = {source.source_id: source for source in bundle.corpus.sources}
    unique = {}
    for citation in citations:
        source = sources[citation.source_id]
        unique[(citation.fragment_id, citation.quote)] = citation.model_dump() | {
            "url": source.url, "publisher": source.publisher, "origin": source.origin,
            "retrieved_at": source.retrieved_at,
        }
    return list(unique.values())


def evidence_json(result: PairExplanation, bundle: ReferenceBundle, retrieval: str) -> dict:
    return {
        "evidence_status": result.explanation_status,
        "explanation": (" ".join(claim.text for claim in result.claims)
                        if result.explanation_status == "SUPPORTED" else result.comment),
        "citations": (_citations([c for claim in result.claims for c in claim.citations], bundle)
                      if result.explanation_status == "SUPPORTED" else []),
        "review_sources": (_citations(result.review_sources, bundle)
                           if result.explanation_status != "SUPPORTED" else []),
        "reason_code": "MANUAL_REVIEW" if result.explanation_status == "SUPPORTED"
        else result.explanation_status,
        "evidence_reasons": result.reasons,
        "effective_mode": "offline",
        "retrieval": retrieval,
    }


def verify_pair(
    check: ItemCheck, *, inn: str | None, diagnosis: str,
    bundle: ReferenceBundle, pipeline: Pipeline,
) -> tuple[dict, PairJob | None]:
    def unresolved(status: str, reason: str, explanation: str):
        return {
            "evidence_status": status, "reason_code": reason, "explanation": explanation,
            "citations": [], "effective_mode": "offline", "retrieval": pipeline.retrieval_method,
        }, None

    if inn and check.review_reason == "NO_RULE_FOR_PAIR":
        material = [EvidenceClaim(fragment, assertion)
                    for fragment in bundle.corpus.fragments
                    if normalize(fragment.inn) == normalize(inn)
                    and normalize(fragment.diagnosis) == normalize(diagnosis)
                    and fragment.required_context
                    for assertion in fragment.assertions if assertion.kind == "CATEGORY"]
        if material:
            evidence, _ = unresolved(
                "INSUFFICIENT_DATA", "MISSING_MEDICAL_CONTEXT",
                "Условный фрагмент требует сведений о схеме сочетанной терапии. "
                "Передайте его специалисту для ручной сверки.",
            )
            evidence["review_sources"] = _citations(
                [citation_for(claim, bundle.corpus) for claim in material], bundle,
            )
            return evidence, None
    if not inn or not check.category:
        return unresolved("INSUFFICIENT_DATA", check.review_reason or "NO_RULE_FOR_PAIR",
                          check.comment or "Требуется сверить исходные данные.")
    category = CATEGORIES[check.category]
    if check.reference_version != bundle.reference_version:
        return unresolved("UNCONFIRMED", "VERSION_MISMATCH", "Версии справочника и корпуса различаются.")
    selected = classify_category(inn, diagnosis, bundle.corpus)
    if selected.status == "CONFLICT":
        return unresolved("SOURCE_CONFLICT", "SOURCE_CONFLICT", "В корпусе конфликтуют основания.")
    fragment = next((f for f in bundle.corpus.fragments if f.fragment_id == check.source), None)
    if (
        selected.category != category or fragment is None
        or fragment.source_id not in selected.source_refs
        or fragment.required_context or normalize(fragment.inn) != normalize(inn)
        or normalize(fragment.diagnosis) != normalize(diagnosis)
        or not any(a.kind == "CATEGORY" and a.category == category
                   and a.upstream_reason == selected.reason for a in fragment.assertions)
    ):
        return unresolved("UNCONFIRMED", "FRAGMENT_MISMATCH", "Точный фрагмент правила не подтверждён.")
    job = PairJob(
        PairContext(diagnosis=diagnosis),
        PairInput(
            item_id=f"{check.item_id}:{check.diagnosis_id}", inn=inn, category=category,
            classification_reason=selected.reason, source_refs=[fragment.fragment_id],
            dosage_check=DosageCheck(status="NOT_CHECKED", reason=None, missing_data=[]),
            upstream_status="PROCESSED", missing_data=[],
        ),
        fragment.fragment_id,
    )
    try:
        result = pipeline.process_pair(job.context, job.item, required_fragment_id=job.fragment_id)
        retrieval = pipeline.retrieval_method
    except (ValueError, RuntimeError, OSError):
        lexical = get_pipeline(bundle.model_dump_json(), "lexical")
        result = lexical.process_pair(job.context, job.item, required_fragment_id=job.fragment_id)
        retrieval = lexical.retrieval_method
    return evidence_json(result, bundle, retrieval), job if result.explanation_status == "SUPPORTED" else None


_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="demo-gigachat")
_SLOTS = BoundedSemaphore(2)
LIVE_BUDGET_SECONDS = 15.0


def live_results(jobs: dict, bundle: ReferenceBundle) -> tuple[dict, str | None]:
    """Обогатить только подтверждённые пары в пределах бюджета HTTP-запроса."""
    if not jobs:
        return {}, "NO_CONFIRMED_PAIRS"
    if not _SLOTS.acquire(blocking=False):
        return {}, "PROVIDER_BUSY"
    deadline = monotonic() + LIVE_BUDGET_SECONDS

    def run():
        results = {}
        # Лексическая выборка повторно проверяет тот же полный набор фрагментов.
        pipeline = get_pipeline(bundle.model_dump_json(), "lexical")
        for key, job in jobs.items():
            left = deadline - monotonic()
            if left <= 0.2:
                results[key] = "GIGACHAT_BUDGET_EXCEEDED"
                continue
            try:
                with GigaChatProvider(
                    os.getenv("GIGACHAT_CREDENTIALS"), access_token=os.getenv("GIGACHAT_ACCESS_TOKEN"),
                    enabled=True, model=os.getenv("GIGACHAT_MODEL", "GigaChat-2"),
                    scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
                    ca_bundle=os.getenv("GIGACHAT_CA_BUNDLE") or None, timeout=min(5.0, left / 2),
                ) as provider:
                    explanation = pipeline.process_pair(
                        job.context, job.item, required_fragment_id=job.fragment_id, provider=provider,
                    )
                results[key] = (explanation if explanation.explanation_status == "SUPPORTED"
                                else "GIGACHAT_UNVERIFIED_RESPONSE")
            except (ValueError, OSError):
                results[key] = "GIGACHAT_CONFIGURATION"
            except GenerationError:
                results[key] = "GIGACHAT_UNVERIFIED_RESPONSE"
            except ProviderError as exc:
                code = str(exc)
                results[key] = code if code in PROVIDER_ERROR_MESSAGES else "GIGACHAT_PROVIDER_ERROR"
        return results

    future = _POOL.submit(run)
    future.add_done_callback(lambda _: _SLOTS.release())
    try:
        return future.result(timeout=LIVE_BUDGET_SECONDS), None
    except TimeoutError:
        return {}, "GIGACHAT_BUDGET_EXCEEDED"
    except Exception:
        return {}, "GIGACHAT_PROVIDER_ERROR"
