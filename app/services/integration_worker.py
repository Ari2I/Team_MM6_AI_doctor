"""Изолированный worker целой заявки. Текущим HTTP-анализом пока не вызывается."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models.enums import ReviewReason
from app.services.integration_protocol import (
    IntegrationProtocolError,
    IntegrationReply,
    IntegrationTask,
    validate_result,
)


def run_request(payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    """Только network: публичный фасад, явный TLS и действующий бюджет заявки."""
    from llm_rag.integration import IntegrationOptions, analyze_request
    from llm_rag.integration_contracts import IntegrationRequest

    request = IntegrationRequest.model_validate(payload)
    result = analyze_request(
        request,
        verify_ssl_certs=True,
        options=IntegrationOptions(deadline_seconds=timeout_seconds),
    )
    return result.model_dump(mode="json")


def execute(task: IntegrationTask) -> IntegrationReply:
    if task.mode != "network":
        return IntegrationReply(request_id=task.request_id, outcome="skipped")
    source = Path(__file__).resolve().parents[2] / "ai" / "src"
    if source.is_dir():
        sys.path.insert(0, str(source))
    try:
        from llm_rag.integration_contracts import IntegrationRequest

        request = IntegrationRequest.model_validate(task.payload)
        if request.request_id != str(task.request_id):
            raise ValueError
    except ImportError:
        return IntegrationReply(
            request_id=task.request_id, outcome="error", error=ReviewReason.AI_UNAVAILABLE
        )
    except (ValidationError, ValueError, TypeError):
        return IntegrationReply(
            request_id=task.request_id, outcome="error", error=ReviewReason.AI_INPUT_INCOMPLETE
        )
    try:
        result = validate_result(run_request(task.payload, task.timeout_seconds), task.payload)
        return IntegrationReply(request_id=task.request_id, outcome="result", result=result)
    except TimeoutError:
        error = ReviewReason.AI_TIMEOUT
    except (
        ValidationError,
        IntegrationProtocolError,
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
    ):
        error = ReviewReason.AI_INVALID_RESPONSE
    except Exception:
        error = ReviewReason.AI_UNAVAILABLE
    return IntegrationReply(request_id=task.request_id, outcome="error", error=error)


@contextlib.contextmanager
def silence_output() -> Iterator[None]:
    """Скрыть Python-вывод и запись в stdout/stderr на уровне дескрипторов ОС."""
    with open(os.devnull, "w", encoding="utf-8") as sink:
        saved = [os.dup(fd) for fd in (1, 2)]
        try:
            for fd in (1, 2):
                os.dup2(sink.fileno(), fd)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                yield
        finally:
            # sys.__stdout__/__stderr__ могут буферизовать вывод мимо redirect_*.
            # Сбрасываем его, пока дескрипторы ещё направлены в /dev/null.
            for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
                if stream is not None:
                    with contextlib.suppress(Exception):
                        stream.flush()
            for fd, original in zip((1, 2), saved, strict=True):
                os.dup2(original, fd)
                os.close(original)


def main() -> None:
    logging.disable(logging.CRITICAL)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    reply = IntegrationReply(
        request_id=None, outcome="error", error=ReviewReason.AI_INPUT_INCOMPLETE
    )
    task: IntegrationTask | None = None
    try:
        with silence_output():
            candidate = IntegrationTask.model_validate_json(sys.stdin.read())
            if "schema_version" not in candidate.model_fields_set:
                raise ValueError
            task = candidate
            reply = execute(task)
    except Exception:
        # Никаких исключений/SDK-ответов и содержимого stdin в stdout/stderr.
        if task is not None:
            reply = IntegrationReply(
                request_id=task.request_id, outcome="error", error=ReviewReason.AI_UNAVAILABLE
            )
    sys.stdout.write(reply.model_dump_json() + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
