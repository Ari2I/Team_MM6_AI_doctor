"""Внутренний JSON-протокол целой заявки; не HTTP-контракт и не пакет провайдеру."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.models.enums import ReviewReason

WorkerError = Literal[
    ReviewReason.AI_INPUT_INCOMPLETE,
    ReviewReason.AI_UNAVAILABLE,
    ReviewReason.AI_TIMEOUT,
    ReviewReason.AI_INVALID_RESPONSE,
]


class IntegrationTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal["integration-worker-request.1"] = "integration-worker-request.1"
    request_id: UUID
    mode: Literal["template", "offline", "network"]
    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    payload: dict[str, Any] = Field(repr=False)


class IntegrationReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal["integration-worker-response.1"] = "integration-worker-response.1"
    request_id: UUID | None
    outcome: Literal["result", "error", "skipped"]
    result: dict[str, Any] | None = Field(default=None, repr=False)
    error: WorkerError | None = None

    @model_validator(mode="after")
    def exclusive_outcome(self) -> "IntegrationReply":
        valid = {
            "result": self.result is not None
            and self.error is None
            and self.request_id is not None,
            "error": self.result is None and self.error is not None,
            "skipped": self.result is None and self.error is None and self.request_id is not None,
        }
        if not valid[self.outcome]:
            raise ValueError("Неверный формат ответа worker.")
        return self


class IntegrationProtocolError(ValueError):
    def __init__(self) -> None:
        super().__init__("Не удалось подтвердить результат ИИ-обработки.")


def validate_result(value: dict[str, Any], expected_request: dict[str, Any]) -> dict[str, Any]:
    """Полная публичная валидация без импорта фасада/SDK и без вывода входа."""
    try:
        from llm_rag.integration_contracts import IntegrationRequest, IntegrationResult

        request = IntegrationRequest.model_validate(expected_request)
        result = IntegrationResult.model_validate(value)
        if result.request != request:
            raise IntegrationProtocolError()
        return result.model_dump(mode="json")
    except (ValidationError, ValueError, TypeError, KeyError):
        raise IntegrationProtocolError() from None
