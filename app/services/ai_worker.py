"""Короткоживущий worker. Запускается только адаптером; не CLI ИИ-модуля."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import AINetworkSettings, AISettings
from app.models.enums import ReviewReason
from app.schemas.analysis import PairCheck
from app.services.ai_adapter import (
    AIInput,
    WorkerResult,
    backend_category,
    module_category,
)

# Данные corpus, а не поля заявки. Произвольный context не принимается.
EVIDENCE_FIELDS = (
    "claim_id",
    "kind",
    "text",
    "quote",
    "fragment_id",
    "source_id",
    "source_title",
    "source_version",
    "location",
    "fragment_text",
)
NETWORK_ITEM_FIELDS = ("item_id", "inn", "diagnosis", "category")


def network_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Разрешённые поля внешнего запроса; dosage/причины/ID заявки исключены."""
    if any(e["kind"] != "CATEGORY" or e.get("required_context") for e in payload["evidence"]):
        raise ValueError("Unsupported evidence")
    return {
        "schema_version": "1.0",
        "is_demo": payload.get("is_demo", False),
        "corpus_version": payload["corpus_version"],
        "item": {key: payload["item"][key] for key in NETWORK_ITEM_FIELDS},
        "evidence": [{key: entry[key] for key in EVIDENCE_FIELDS} for entry in payload["evidence"]],
    }


def sent_field_names(payload: dict[str, Any]) -> list[str]:
    return sorted(
        ["schema_version", "is_demo", "corpus_version"]
        + [f"item.{key}" for key in payload["item"]]
        + [f"evidence[].{key}" for key in EVIDENCE_FIELDS]
    )


class AllowlistedProvider:
    name = "backend-allowlisted-gigachat"

    def __init__(self, provider: Any, progress: Callable[..., None] | None = None) -> None:
        self.provider = provider
        self.sent_fields: list[str] = []
        self.progress = progress

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        filtered = network_payload(payload)
        self.sent_fields = sent_field_names(filtered)
        if self.progress:
            self.progress(sent_fields=self.sent_fields)
        try:
            return self.provider.generate(filtered)
        finally:
            if self.progress:
                self.progress(
                    sent_fields=self.sent_fields,
                    tokens=self.provider.usage_totals.get("total_tokens") or None,
                )


def _deny_network(event: str, args: tuple[Any, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise PermissionError("Offline mode")


def run_module(
    pair: AIInput,
    local: PairCheck,
    settings: AISettings,
    timeout: float,
    progress: Callable[..., None] | None = None,
) -> WorkerResult:
    """Ленивые импорты; template сюда не приходит и пакета не требует."""
    if settings.registry_path or settings.clinical_cache_dir or settings.live_sources:
        source = Path(__file__).resolve().parents[2] / "ai" / "src"
        if source.is_dir():
            sys.path.insert(0, str(source))
        from app.services.ml2_analysis import analyze_pair

        return analyze_pair(pair, local, settings, timeout, progress)
    if not settings.corpus_path:
        from app.services.standard_analysis import analyze_pair

        return analyze_pair(pair, local, settings, timeout, progress)
    # Источники подключаются как библиотека без выполнения CLI, загрузки .env,
    # установки тяжёлых зависимостей или скачивания моделей.
    source = Path(__file__).resolve().parents[2] / "ai" / "src"
    if source.is_dir():
        sys.path.insert(0, str(source))
    from llm_rag.contracts import AnalysisRequest, AnalysisResult
    from llm_rag.corpus import Corpus, normalize
    from llm_rag.pipeline import Pipeline
    from llm_rag.providers.demo import DemoProvider

    corpus = Corpus.model_validate_json(Path(settings.corpus_path).read_text(encoding="utf-8"))
    candidates = [
        assertion
        for fragment in corpus.fragments
        if normalize(fragment.inn) == normalize(pair.mnn or "")
        and normalize(fragment.diagnosis) == normalize(pair.diagnosis)
        and not fragment.required_context
        for assertion in fragment.assertions
        if assertion.kind == "CATEGORY"
    ]
    category = module_category(local.category)
    if category is None and len({a.category for a in candidates}) == 1:
        category = candidates[0].category
    # Передаём только точную причину из corpus для выбранной категории.
    reasons = {a.upstream_reason for a in candidates if a.category == category}
    reason = next(iter(reasons)) if len(reasons) == 1 else None
    request = AnalysisRequest.model_validate(
        {
            "schema_version": "1.0",
            "request_id": "pair",
            "is_demo": True,
            "diagnosis": pair.diagnosis,
            "items": [
                {
                    "item_id": "item",
                    "inn": pair.mnn,
                    "category": category,
                    "original": {
                        "trade_name": pair.drug_name,
                        "dosage": pair.dosage,
                        "quantity": int(pair.quantity),
                        "amount": str(pair.amount),
                        "currency": settings.currency,
                    },
                    "classification_reason": reason,
                    "dosage_check": {"status": "NOT_CHECKED", "reason": None, "missing_data": []},
                    "source_refs": [],
                    "upstream_status": "PROCESSED",
                    "missing_data": [],
                }
            ],
        }
    )
    provider: Any = DemoProvider()
    network: Any = None
    try:
        if settings.mode == "network":
            from llm_rag.providers.gigachat import GigaChatProvider

            try:
                credentials = AINetworkSettings()
                network = GigaChatProvider(
                    credentials=credentials.credentials.get_secret_value(),
                    enabled=True,
                    verify_ssl_certs=True,
                    timeout=timeout,
                    scope=credentials.scope or "GIGACHAT_API_PERS",
                    ca_bundle=credentials.ca_bundle_file or None,
                )
            except Exception:
                return WorkerResult(error=ReviewReason.AI_UNAVAILABLE.value)
            provider = AllowlistedProvider(network, progress)
        result = Pipeline(corpus, provider, embedder=None).run(request)
        try:
            result = AnalysisResult.model_validate(result.model_dump())
        except (AttributeError, ValidationError, ValueError, TypeError):
            return WorkerResult(error=ReviewReason.AI_INVALID_RESPONSE.value)
        if (
            result.request_id != "pair"
            or result.diagnosis != pair.diagnosis
            or len(result.items) != 1
            or result.corpus_version != corpus.corpus_version
        ):
            raise ValueError("Invalid result identity")
        item = result.items[0]
        if (
            item.item_id != "item"
            or item.category != category
            or item.inn != pair.mnn
            or item.original != request.items[0].original
        ):
            raise ValueError("Changed upstream result")
        metadata = {
            "sent_fields": provider.sent_fields if network else [],
            "tokens": (network.usage_totals.get("total_tokens") or None) if network else None,
        }
        if item.explanation_status == "PROCESSING_ERROR":
            code = ReviewReason.AI_UNAVAILABLE
            if any("TIMEOUT" in r for r in item.reasons):
                code = ReviewReason.AI_TIMEOUT
            elif any("INVALID" in r or "GENERATION" in r for r in item.reasons):
                code = ReviewReason.AI_INVALID_RESPONSE
            return WorkerResult(error=code.value, **metadata)
        if any(r.startswith("GENERATION_") for r in item.reasons):
            return WorkerResult(error=ReviewReason.AI_INVALID_RESPONSE.value, **metadata)
        if item.explanation_status == "SUPPORTED" and not item.claims:
            raise ValueError("Missing evidence")
        source = (
            "; ".join(
                sorted(
                    {
                        f"{citation.title} [{citation.source_id}/{citation.fragment_id}; "
                        f"{citation.version}]"
                        for claim in item.claims
                        for citation in claim.citations
                    }
                )
            )
            or "Синтетический корпус ИИ; подтверждённое основание отсутствует"
        )
        review = item.explanation_status != "SUPPORTED" or local.category is None
        check = PairCheck(
            category=(
                backend_category(item.category)
                if local.category is not None or item.explanation_status == "SUPPORTED"
                else None
            ),
            source=source,
            reference_version=result.corpus_version,
            comment=item.comment,
            needs_review=review,
            review_reason=ReviewReason.AI_REVIEW_REQUIRED if review else None,
        )
        return WorkerResult(check=check.model_dump(mode="json"), **metadata)
    finally:
        if network is not None:
            network.close()


def main() -> None:
    # Worker не пишет содержимое заявки, ответ провайдера или сообщения SDK в логи.
    logging.disable(logging.CRITICAL)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    output = sys.stdout

    def progress(**metadata: Any) -> None:
        output.write("AI_META " + WorkerResult(**metadata).model_dump_json() + "\n")
        output.flush()

    with open(os.devnull, "w") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            result = execute(json.load(sys.stdin), guard_network=True, progress=progress)
    output.write(result.model_dump_json())


def execute(
    data: dict[str, Any],
    *,
    guard_network: bool = False,
    progress: Callable[..., None] | None = None,
) -> WorkerResult:
    try:
        settings = AISettings.model_validate(data["settings"])
        if guard_network and settings.mode == "offline":
            sys.addaudithook(_deny_network)
        elif guard_network and settings.mode == "template" and not settings.live_sources:
            sys.addaudithook(_deny_network)
        return run_module(
            AIInput.model_validate(data["pair"]),
            PairCheck.model_validate(data["local"]),
            settings,
            data["timeout"],
            progress,
        )
    except (ValidationError, ValueError, TypeError, KeyError):
        return WorkerResult(error=ReviewReason.AI_INVALID_RESPONSE.value)
    except TimeoutError:
        return WorkerResult(error=ReviewReason.AI_TIMEOUT.value)
    except Exception:
        return WorkerResult(error=ReviewReason.AI_UNAVAILABLE.value)


if __name__ == "__main__":
    main()
