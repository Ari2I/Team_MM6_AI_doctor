"""Один network-worker на заявку, сохранение снимка и проекция в прежние проверки."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import REVIEW_REASON_LABELS, AISettings
from app.models import Application
from app.models.enums import DrugCategory, ReviewReason
from app.schemas.analysis import PairCheck, ReferenceSnapshot
from app.services.ai_reports import AIReportError, save_ai_result
from app.services.integration_client import IntegrationWorkerClient
from app.services.integration_mapping import IntegrationMappingError, prepare_integration_request


@dataclass(repr=False)
class ApplicationAI:
    """Только проверенные пары или общая безопасная причина fallback."""

    pairs: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    error: ReviewReason | None = None


def analyze_network_application(
    session: Session, application: Application, settings: AISettings, snapshot: ReferenceSnapshot
) -> ApplicationAI:
    """Сохранить только полностью возвращённый результат; commit делает оркестратор."""
    if not settings.backend_revision or not settings.library_revision:
        return ApplicationAI(error=ReviewReason.AI_UNAVAILABLE)
    try:
        prepared = prepare_integration_request(application, currency=settings.currency)
        reply = IntegrationWorkerClient(settings).analyze(prepared)
        if reply.error:
            return ApplicationAI(error=ReviewReason(reply.error))
        if reply.outcome != "result" or reply.result is None:
            return ApplicationAI(error=ReviewReason.AI_INVALID_RESPONSE)
        # Ошибка сохранения не должна оставлять наполовину записанный AI-отчёт
        # или уничтожать детерминированную карточку. Внешняя транзакция остаётся.
        with session.begin_nested():
            stored = save_ai_result(
                session,
                application.id,
                reply.result,
                currency=settings.currency,
                backend_revision=settings.backend_revision,
                ai_revision=settings.library_revision,
                snapshot=snapshot,
            )
            result = stored.result
            pairs = {(pair["item_id"], pair["diagnosis_id"]): pair for pair in result["pairs"]}
            for pair in result["unprocessed_pairs"]:
                pairs[pair["item_id"], pair["diagnosis_id"]] = pair
            return ApplicationAI(pairs=pairs)
    except IntegrationMappingError as error:
        return ApplicationAI(error=error.review_reason)
    except (AIReportError, ValueError, TypeError, KeyError):
        return ApplicationAI(error=ReviewReason.AI_INVALID_RESPONSE)
    except Exception:
        # Текст исключения, stdout/stderr и входные данные никогда не выводятся.
        return ApplicationAI(error=ReviewReason.AI_UNAVAILABLE)


def fallback_check(local: PairCheck, reason: ReviewReason) -> PairCheck:
    return local.model_copy(
        update={
            "needs_review": True,
            "review_reason": reason,
            "comment": " ".join(
                part for part in (local.comment, REVIEW_REASON_LABELS[reason]) if part
            ),
        }
    )


def project_pair(
    local: PairCheck, outcome: ApplicationAI, item_id: str, diagnosis_id: str
) -> PairCheck:
    """SQL-правило приоритетно; AI-категория остаётся предложением на ручную сверку."""
    if outcome.error:
        return fallback_check(local, outcome.error)
    pair = outcome.pairs.get((item_id, diagnosis_id))
    if pair is None:
        return fallback_check(local, ReviewReason.AI_INVALID_RESPONSE)
    if "decision" not in pair:
        reason = _failure_reason([pair["reason"]])
        return fallback_check(local, reason)

    decision = pair["decision"]
    reasons = pair["reasons"]
    failure = _failure_reason(reasons)
    conflict = decision["classification_basis"] == "CONFLICT" or bool(
        local.category
        and any(
            category and category != local.category
            for category in (decision["upstream_rule_category"], decision["proposed_category"])
        )
    )
    category = local.category
    source = local.source
    version = local.reference_version
    proposed = decision["upstream_rule_category"] or decision["proposed_category"]
    if category is None and not conflict and failure == ReviewReason.AI_REVIEW_REQUIRED:
        category = DrugCategory(proposed) if proposed else None
        if category:
            source = "Предложение AI; требуется проверка человеком"
            version = pair["policy_version"]

    reason = failure
    if conflict:
        reason = ReviewReason.AI_REVIEW_REQUIRED
    elif failure == ReviewReason.AI_REVIEW_REQUIRED and category and local.category is None:
        reason = ReviewReason.AI_PRELIMINARY
    comments = [
        local.comment if local.category or not category else None,
        REVIEW_REASON_LABELS[reason],
    ]
    if conflict:
        comments.append(
            "AI-категория расходится с правилом или другим AI-основанием; сверьте источники."
        )
    # Не копировать произвольные модельные тексты в старую карточку: полный
    # human_report и проверенные цитаты уже сохранены в отдельном AI-отчёте.
    if pair["form_status"] == "MISMATCH":
        comments.append(
            "Обнаружено расхождение формы или концентрации. Сверьте препарат с источником; "
            "это отдельное предупреждение, оно не меняет категорию."
        )
    return PairCheck(
        category=category,
        source=source,
        reference_version=version,
        comment=" ".join(part for part in comments if part),
        needs_review=True,
        review_reason=reason,
    )


def resolved_inn(outcome: ApplicationAI, item_id: str) -> str | None:
    """Не выбрать произвольное МНН при расхождении результатов разных диагнозов."""
    values = {
        pair["inn"]
        for (identity, _), pair in outcome.pairs.items()
        if identity == item_id and pair.get("inn")
    }
    return next(iter(values)) if len(values) == 1 and outcome.error is None else None


def _failure_reason(reasons: list[str]) -> ReviewReason:
    if any(reason in {"AI_TIMEOUT", "BUDGET_EXHAUSTED"} for reason in reasons):
        return ReviewReason.AI_TIMEOUT
    if "AI_INVALID_RESPONSE" in reasons:
        return ReviewReason.AI_INVALID_RESPONSE
    if any(
        reason
        in {
            "AI_UNAVAILABLE",
            "SOURCE_ERROR",
            "INTERNAL_ERROR",
            "NETWORK_POLICY_DENIED",
            "MODEL_DISABLED",
        }
        for reason in reasons
    ):
        return ReviewReason.AI_UNAVAILABLE
    return ReviewReason.AI_REVIEW_REQUIRED
