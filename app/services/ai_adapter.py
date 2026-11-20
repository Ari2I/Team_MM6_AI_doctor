"""Граница backend/AI: изолированный вызов на пару, локальные правила приоритетны."""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import REVIEW_REASON_LABELS, AISettings
from app.models.enums import DrugCategory, ReviewReason
from app.schemas.analysis import PairCheck


def module_category(category: DrugCategory | None) -> str | None:
    return category.value if category is not None else None


def backend_category(category: str | None) -> DrugCategory | None:
    if category is None:
        return None
    return DrugCategory(category)


class AIInput(BaseModel):
    """Явный список локальных полей; исходные ID, файл и context отсутствуют."""

    drug_name: str
    mnn: str | None = None
    diagnosis: str
    dosage: str | None = None
    quantity: Decimal | None = None
    amount: Decimal | None = None
    age_group: str | None = None

    def complete(self) -> bool:
        return bool(
            self.drug_name.strip()
            and self.diagnosis.strip()
            and self.dosage
            and self.dosage.strip()
            and self.quantity is not None
            and self.quantity.is_finite()
            and self.quantity > 0
            and self.quantity == self.quantity.to_integral_value()
            and self.amount is not None
            and self.amount.is_finite()
            and self.amount >= 0
        )


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    check: dict[str, Any] | None = None
    error: str | None = None
    tokens: int | None = Field(default=None, ge=0)
    sent_fields: list[str] = Field(default_factory=list)
    mnn: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class AIAdapter:
    def __init__(self, settings: AISettings | None = None) -> None:
        self.settings = settings or AISettings()
        self.deadline = monotonic() + self.settings.application_timeout_seconds
        self.last_tokens: int | None = None
        self.last_sent_fields: list[str] = []
        self.last_mnn: str | None = None
        self.last_diagnostics: dict[str, Any] = {}

    def _read_metadata(self, output: str | bytes) -> str:
        if isinstance(output, bytes):
            output = output.decode("utf-8")
        result = []
        for line in output.splitlines():
            if line.startswith("AI_META "):
                metadata = WorkerResult.model_validate_json(line.removeprefix("AI_META "))
                self.last_sent_fields = metadata.sent_fields
                self.last_tokens = metadata.tokens
            else:
                result.append(line)
        return "\n".join(result)

    def _fallback(self, local: PairCheck, reason: ReviewReason) -> PairCheck:
        return local.model_copy(
            update={
                "needs_review": True,
                "review_reason": reason,
                "comment": " ".join(
                    part for part in (local.comment, REVIEW_REASON_LABELS[reason]) if part
                ),
            }
        )

    def analyze(self, pair: AIInput, local: PairCheck) -> PairCheck:
        self.last_tokens = None
        self.last_sent_fields = []
        self.last_mnn = None
        self.last_diagnostics = {}
        if self.settings.mode == "template" and not (
            self.settings.registry_path
            or self.settings.clinical_cache_dir
            or self.settings.live_sources
        ):
            return local
        if not pair.drug_name.strip() or not pair.diagnosis.strip():
            return self._fallback(local, ReviewReason.AI_INPUT_INCOMPLETE)
        # The legacy corpus contract requires complete purchase fields. Ordinary
        # pair analysis never depends on dosage, quantity or amount.
        if self.settings.corpus_path and not self.settings.registry_path and not pair.complete():
            return self._fallback(local, ReviewReason.AI_INPUT_INCOMPLETE)
        budget = min(self.settings.pair_timeout_seconds, self.deadline - monotonic())
        if budget <= 0:
            return self._fallback(local, ReviewReason.AI_TIMEOUT)
        try:
            # subprocess.run убивает и дожидается worker при таймауте: фоновых
            # вызовов после отката нет. Импорт модуля тоже входит в бюджет.
            completed = subprocess.run(
                [sys.executable, "-m", "app.services.ai_worker"],
                input=json.dumps(
                    {
                        "pair": pair.model_dump(mode="json"),
                        "local": local.model_dump(mode="json"),
                        "settings": self.settings.model_dump(mode="json"),
                        "timeout": budget,
                    }
                ),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=budget,
                check=True,
            )
            result = WorkerResult.model_validate_json(self._read_metadata(completed.stdout))
            self.last_mnn = result.mnn
            self.last_diagnostics = result.diagnostics
            if result.tokens is not None:
                self.last_tokens = result.tokens
            self.last_sent_fields = result.sent_fields or self.last_sent_fields
            if result.error:
                reason = ReviewReason(result.error)
                if reason not in {
                    ReviewReason.AI_UNAVAILABLE,
                    ReviewReason.AI_TIMEOUT,
                    ReviewReason.AI_INVALID_RESPONSE,
                }:
                    raise ValueError
                return self._fallback(local, reason)
            if result.check is None:
                raise ValueError
            ai = PairCheck.model_validate(result.check)
            if not ai.source or not ai.reference_version or not ai.comment:
                raise ValueError
            if monotonic() >= self.deadline:
                return self._fallback(local, ReviewReason.AI_TIMEOUT)
            if local.category is not None:
                # Никогда не перезаписывать подтверждённую категорию/источник/версию.
                return local.model_copy(
                    update={
                        "comment": ai.comment,
                        "needs_review": local.needs_review or ai.needs_review,
                        "review_reason": local.review_reason or ai.review_reason,
                    }
                )
            ai.needs_review = True
            ai.review_reason = (
                ReviewReason.AI_PRELIMINARY
                if ai.category
                else ai.review_reason or local.review_reason
            ) or ReviewReason.AI_REVIEW_REQUIRED
            return ai
        except subprocess.TimeoutExpired as error:
            if error.stdout:
                try:
                    self._read_metadata(error.stdout)
                except (ValidationError, ValueError, TypeError):
                    pass
            return self._fallback(local, ReviewReason.AI_TIMEOUT)
        except (ValidationError, ValueError, TypeError):
            return self._fallback(local, ReviewReason.AI_INVALID_RESPONSE)
        except Exception:
            # Ни текст исключения, ни stdout/stderr worker не попадают в логи/API.
            return self._fallback(local, ReviewReason.AI_UNAVAILABLE)
