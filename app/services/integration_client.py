"""Клиент отдельного worker всей заявки; не подключён к HTTP-оркестрации."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from time import monotonic
from uuid import UUID

from pydantic import ValidationError

from app.config import AISettings
from app.models.enums import ReviewReason
from app.services.integration_mapping import (
    IntegrationMappingError,
    PreparedIntegrationRequest,
    validate_integration_request,
)
from app.services.integration_protocol import (
    IntegrationProtocolError,
    IntegrationReply,
    IntegrationTask,
    validate_result,
)


class IntegrationWorkerClient:
    def __init__(self, settings: AISettings) -> None:
        self.settings = settings

    def analyze(self, prepared: PreparedIntegrationRequest) -> IntegrationReply:
        """Один процесс на заявку, бюджет из существующего application_timeout_seconds."""
        identifier: UUID | None = None

        def failure(reason: ReviewReason) -> IntegrationReply:
            return IntegrationReply(request_id=identifier, outcome="error", error=reason)

        try:
            identifier = UUID(prepared.payload["request_id"])
        except (ValueError, TypeError, KeyError, AttributeError):
            return failure(ReviewReason.AI_INPUT_INCOMPLETE)
        if self.settings.mode != "network":
            return IntegrationReply(request_id=identifier, outcome="skipped")
        deadline = monotonic() + self.settings.application_timeout_seconds
        try:
            source = Path(__file__).resolve().parents[2] / "ai" / "src"
            if source.is_dir() and str(source) not in sys.path:
                sys.path.insert(0, str(source))
            payload = validate_integration_request(prepared)
            budget = deadline - monotonic()
            if budget <= 0:
                return failure(ReviewReason.AI_TIMEOUT)
            task = IntegrationTask(
                request_id=identifier, mode="network", timeout_seconds=budget, payload=payload
            )
            completed = subprocess.run(
                [sys.executable, "-m", "app.services.integration_worker"],
                input=task.model_dump_json(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=budget,
                check=True,
            )
            # stdout — ровно один JSON; никакие частичные stdout при ошибке не принимаются.
            reply = IntegrationReply.model_validate_json(completed.stdout)
            if (
                "schema_version" not in reply.model_fields_set
                or reply.request_id != identifier
                or reply.outcome == "skipped"
            ):
                return failure(ReviewReason.AI_INVALID_RESPONSE)
            if reply.result is not None:
                reply.result = validate_result(reply.result, payload)
            if monotonic() >= deadline:
                return failure(ReviewReason.AI_TIMEOUT)
            return reply
        except IntegrationMappingError as error:
            return failure(error.review_reason)
        except subprocess.TimeoutExpired:
            # subprocess.run убивает и дожидается worker. Его частичный вывод отбрасывается.
            return failure(ReviewReason.AI_TIMEOUT)
        except (subprocess.CalledProcessError, OSError, ImportError):
            return failure(ReviewReason.AI_UNAVAILABLE)
        except (ValidationError, IntegrationProtocolError, ValueError, TypeError, UnicodeError):
            return failure(ReviewReason.AI_INVALID_RESPONSE)
