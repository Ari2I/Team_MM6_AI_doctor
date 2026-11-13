"""JSON-адаптер ML-2 поверх существующего клиента коллеги, без его изменения."""

import json
import ssl

import httpx
from gigachat.exceptions import GigaChatException, ResponseError
from gigachat.models import Chat
from pydantic import ValidationError
from jsonschema import Draft202012Validator

from llm_rag.providers.base import ProviderError
from llm_rag.providers.gigachat import GigaChatProvider


def _certificate_failure(error: Exception) -> bool:
    """Определить ошибку сертификата по цепочке причин, не раскрывая текст исключений."""
    cause = error
    for _ in range(8):
        if isinstance(cause, ssl.SSLCertVerificationError):
            return True
        cause = cause.__cause__ or cause.__context__
        if cause is None:
            break
    return False


def _json_object(body: bytes | str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Повторяющийся ключ JSON")
            result[key] = value
        return result

    try:
        result = json.loads(body, object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise ProviderError("GIGACHAT_INVALID_JSON") from None
    if not isinstance(result, dict):
        raise ProviderError("GIGACHAT_INVALID_JSON_OBJECT")
    return result


class ML2GigaChatProvider(GigaChatProvider):
    """Наследует авторизацию и закрытие соединений из клиента LLM/RAG."""

    def complete_json(self, context: dict, instructions: str, schema: dict) -> dict:
        self.last_total_tokens = None
        try:
            request = Chat(
                model=self._model,
                stream=False,
                function_call="none",
                max_tokens=2048,
                messages=[
                    {"role": "system", "content": instructions},
                    {
                        "role": "user",
                        "content": json.dumps(
                            context, ensure_ascii=False, allow_nan=False
                        ),
                    },
                ],
                # В SDK 0.2.0 дополнительные параметры передаются через additional_fields.
                additional_fields={
                    "response_format": {
                        "type": "json_schema",
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
            response = self._client.chat(request)
            total_tokens = getattr(
                getattr(response, "usage", None), "total_tokens", None
            )
            if type(total_tokens) is int and total_tokens >= 0:
                self.last_total_tokens = total_tokens
            if len(response.choices) != 1:
                raise ProviderError("GIGACHAT_INVALID_COMPLETION")
            choice = response.choices[0]
            if choice.finish_reason != "stop":
                raise ProviderError("GIGACHAT_INCOMPLETE_COMPLETION")
            message = choice.message
            if message.role != "assistant" or not isinstance(message.content, str):
                raise ProviderError("GIGACHAT_INVALID_COMPLETION")
            if len(message.content.encode("utf-8")) > 1_000_000:
                raise ProviderError("GIGACHAT_RESPONSE_TOO_LARGE")
            result = _json_object(message.content)
            # response_format сервера не заменяет независимую локальную проверку.
            if not Draft202012Validator(schema).is_valid(result):
                raise ProviderError("GIGACHAT_INVALID_RESPONSE")
            return result
        except httpx.TimeoutException:
            raise ProviderError("GIGACHAT_TIMEOUT") from None
        except httpx.HTTPError as error:
            if _certificate_failure(error):
                raise ProviderError("GIGACHAT_TLS_ERROR") from None
            raise ProviderError("GIGACHAT_UNAVAILABLE") from None
        except ResponseError as error:
            codes = {
                400: "GIGACHAT_BAD_REQUEST",
                401: "GIGACHAT_AUTH_FAILED",
                403: "GIGACHAT_FORBIDDEN",
                404: "GIGACHAT_NOT_FOUND",
                422: "GIGACHAT_BAD_REQUEST",
                429: "GIGACHAT_RATE_LIMIT",
            }
            code = (
                "GIGACHAT_SERVER_ERROR"
                if error.status_code >= 500
                else codes.get(error.status_code, "GIGACHAT_API_ERROR")
            )
            raise ProviderError(code) from None
        except GigaChatException:
            raise ProviderError("GIGACHAT_API_ERROR") from None
        except (ValidationError, ValueError, UnicodeError):
            raise ProviderError("GIGACHAT_INVALID_RESPONSE") from None
