"""Pair analysis against the active reference, without synthetic data contracts."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from app.config import AINetworkSettings, AISettings
from app.models.enums import ReviewReason
from app.schemas.analysis import PairCheck
from app.services.ai_adapter import AIInput, WorkerResult


class LLMProvider(Protocol):
    usage_totals: dict[str, int]

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def close(self) -> None: ...


def create_provider(timeout: float) -> LLMProvider:
    source = Path(__file__).resolve().parents[2] / "ai" / "src"
    if source.is_dir():
        sys.path.insert(0, str(source))
    from llm_rag.network_policy import NetworkPolicy
    from llm_rag.providers.gigachat import GigaChatProvider

    credentials = AINetworkSettings()
    return GigaChatProvider(
        credentials=credentials.credentials.get_secret_value(),
        enabled=True,
        verify_ssl_certs=True,
        ca_bundle=credentials.ca_bundle_file or None,
        scope=credentials.scope or "GIGACHAT_API_PERS",
        timeout=timeout,
        network_policy=NetworkPolicy(),
    )


def analyze_pair(
    pair: AIInput,
    local: PairCheck,
    settings: AISettings,
    timeout: float,
    progress: Callable[..., None] | None = None,
) -> WorkerResult:
    if not (pair.mnn and local.category and local.source and local.reference_version):
        unresolved = local.model_copy(
            update={
                "needs_review": True,
                "review_reason": local.review_reason or ReviewReason.NO_RULE_FOR_PAIR,
                "source": local.source or "Подтверждённое основание не найдено",
                "reference_version": local.reference_version or "unconfigured",
                "comment": local.comment
                or "Категория не определена: подтверждённое правило не найдено. "
                "Требуется проверка человеком.",
            }
        )
        return WorkerResult(check=unresolved.model_dump(mode="json"))

    statement = (
        f"Согласно правилу справочника для пары «{pair.mnn} — {pair.diagnosis}» "
        f"указана категория {local.category.value}. "
        f"Источник: {local.source}; версия: {local.reference_version}."
    )
    result = local.model_copy(update={"comment": statement})
    if settings.mode != "network":
        return WorkerResult(check=result.model_dump(mode="json"))

    # The LLM only reproduces a supplied rule. It never assigns a category or
    # creates medical evidence. Purchase fields are not included in this payload.
    claim = {"claim_id": "rule", "text": statement, "fragment_id": "rule", "quote": statement}
    payload = {
        "schema_version": "1.0",
        "corpus_version": local.reference_version,
        "item": {
            "item_id": "item",
            "inn": pair.mnn,
            "diagnosis": pair.diagnosis,
            "category": local.category.value,
        },
        "evidence": [
            {
                **claim,
                "kind": "CATEGORY",
                "source_id": "reference",
                "source_title": local.source,
                "source_version": local.reference_version,
                "location": "Правило справочника для пары МНН и диагноза",
                "fragment_text": statement,
            }
        ],
    }
    from app.services.ai_worker import AllowlistedProvider

    provider = create_provider(timeout)
    guarded = AllowlistedProvider(provider, progress)
    try:
        try:
            response = guarded.generate(payload)
        except Exception as error:
            reason = (
                error.args[0] if len(error.args) == 1 and isinstance(error.args[0], str) else ""
            )
            code = (
                ReviewReason.AI_TIMEOUT
                if isinstance(error, TimeoutError) or reason == "GIGACHAT_TIMEOUT"
                else ReviewReason.AI_INVALID_RESPONSE
                if reason in {"GIGACHAT_INVALID_JSON", "GIGACHAT_INVALID_RESPONSE"}
                else ReviewReason.AI_UNAVAILABLE
            )
            return WorkerResult(
                error=code.value,
                sent_fields=guarded.sent_fields,
                tokens=provider.usage_totals.get("total_tokens") or None,
            )
        if response != {"item_id": "item", "claims": [claim]}:
            return WorkerResult(
                error=ReviewReason.AI_INVALID_RESPONSE.value,
                sent_fields=guarded.sent_fields,
                tokens=provider.usage_totals.get("total_tokens") or None,
            )
        return WorkerResult(
            check=result.model_dump(mode="json"),
            sent_fields=guarded.sent_fields,
            tokens=provider.usage_totals.get("total_tokens") or None,
        )
    finally:
        provider.close()
