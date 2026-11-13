"""Генерация через официальный SDK GigaChat без вызовов эмбеддингов."""

import json
import math
import ssl
from time import perf_counter

from llm_rag.diagnostics import safe_diagnostic

from gigachat import GigaChat
from gigachat.exceptions import GigaChatException, ResponseError
from gigachat.models import Chat
import httpx
from pydantic import ValidationError

from llm_rag.generation import INSTRUCTIONS
from llm_rag.providers.base import ProviderError
from llm_rag.validation import GeneratedResponse


AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
BASE_URL = "https://api.giga.chat/v1"
SCOPES = {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}


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


class GigaChatProvider:
    """Один экземпляр для последовательного конвейера. Секреты и токены хранятся в памяти."""

    name = "gigachat-api"

    def __init__(
        self, credentials: str | None = None, *, access_token: str | None = None,
        enabled: bool = False, model: str = "GigaChat-2", scope: str = "GIGACHAT_API_PERS",
        timeout: float = 20.0, ca_bundle: str | None = None,
        verify_ssl_certs: bool = True,
    ):
        if not enabled or bool(credentials) == bool(access_token):
            raise ValueError("Включите API и задайте только GIGACHAT_CREDENTIALS или GIGACHAT_ACCESS_TOKEN")
        if scope not in SCOPES or not model.strip() or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Некорректный scope, модель или тайм-аут GigaChat")
        secret = credentials or access_token
        if not isinstance(secret, str) or not secret.strip() or not secret.isascii() or any(c.isspace() for c in secret):
            raise ValueError("Некорректный формат ключа GigaChat")
        if type(verify_ssl_certs) is not bool:
            raise ValueError("Проверка сертификата должна задаваться логическим значением")
        self._timeout = timeout
        self.last_diagnostic = None
        self._model = model
        self.usage_totals = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        tls = ssl.create_default_context() if verify_ssl_certs else None
        if tls is not None and ca_bundle:
            tls.load_verify_locations(cafile=ca_bundle)
        # Пустая строка отключает подстановку второго способа авторизации из окружения SDK.
        self._client = GigaChat(
            credentials=credentials or "", access_token=access_token or "",
            base_url=BASE_URL, auth_url=AUTH_URL, model=model, scope=scope,
            timeout=timeout, verify_ssl_certs=verify_ssl_certs, ssl_context=tls,
            ca_bundle_file="", max_retries=0,
        )

    def close(self) -> None:
        """Закрыть сетевые соединения SDK после обработки заявки."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def generate(self, payload: dict) -> dict:
        started = perf_counter()
        self.last_diagnostic = None
        self._diagnostic_stage = 'request_preparation'
        try:
            return self._generate(payload)
        except Exception as error:
            self.last_diagnostic = safe_diagnostic(error, stage=self._diagnostic_stage,
                elapsed_ms=(perf_counter() - started) * 1000)
            self.last_diagnostic['timeout_seconds'] = self._timeout
            raise

    def _generate(self, payload: dict) -> dict:
        if payload.get("is_demo") is not True:
            raise ProviderError("DEMO_DATA_REQUIRED")
        wire_mode = payload.get('schema_version') == 'staff-facts.2'
        staff_mode = wire_mode
        instructions = INSTRUCTIONS
        if wire_mode:
            from llm_rag.staff_report import STAFF_WIRE_INSTRUCTIONS
            from llm_rag.staff_contracts import StaffFacts, StaffWireAnalysis, staff_wire_schema
            facts = StaffFacts.model_validate(payload)
            if len(facts.pairs) != 1:
                raise ProviderError('GIGACHAT_INVALID_RESPONSE')
            packet = facts.pairs[0]
            schema = staff_wire_schema(packet)
            instructions = STAFF_WIRE_INSTRUCTIONS
            context = facts.model_dump(mode='json')
        else:
            schema = GeneratedResponse.model_json_schema()
            # Структурированный ответ должен относиться только к обрабатываемой позиции.
            schema["properties"]["item_id"] = {"type": "string", "enum": [payload["item"]["item_id"]]}
            schema.pop("$defs")
            # Не передаём общий шаблон \S: генератор GigaChat ограничивает им строку целиком.
            # Значения и связи полей берём из уже проверенного пакета оснований.
            alternatives = [{
                "type": "object", "additionalProperties": False,
                "properties": {key: {"type": "string", "enum": [claim[key]]}
                               for key in ("claim_id", "text", "fragment_id", "quote")},
                "required": ["claim_id", "text", "fragment_id", "quote"],
            } for claim in payload["evidence"]]
            staff_mode = payload.get('schema_version') == 'staff-report.1'
            if staff_mode:
                for alternative, claim in zip(alternatives, payload['evidence']):
                    alternative['properties']['text']['enum'] = [claim['text'], *claim.get('allowed_texts', [])]
            schema["properties"]["claims"] = {
                "type": "array", "minItems": len(alternatives), "maxItems": len(alternatives),
                "uniqueItems": True,
                "items": (alternatives[0] if len(alternatives) == 1 else {"oneOf": alternatives})
                         if alternatives else {"type": "object"},
            }
            # Инструкции заданы кодом и не берутся из входного запроса или текста документов.
            context = {key: payload[key] for key in ("schema_version", "is_demo", "corpus_version", "item", "evidence")}
        try:
            request = Chat(
                model=self._model, stream=False, function_call="none", max_tokens=1500 if wire_mode else (4096 if staff_mode else 2048),
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False, allow_nan=False)},
                ],
                # В SDK 0.2.0 дополнительные параметры передаются через additional_fields.
                additional_fields={"response_format": {"type": "json_schema", "schema": schema, "strict": True}},
            )
            self._diagnostic_stage = 'provider_request'
            response = self._client.chat(request)
            self._diagnostic_stage = 'provider_response'
            # Только счётчики из ответа API, без текста запроса и учётных данных.
            if response.usage is not None:
                for key in self.usage_totals:
                    value = getattr(response.usage, key, None)
                    if isinstance(value, int) and value >= 0:
                        self.usage_totals[key] += value
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
            raw = _json_object(message.content)
            if wire_mode:
                wire = StaffWireAnalysis.model_validate(raw)
                if wire.pair_id != packet.pair_id:
                    raise ProviderError('RESPONSE_ITEM_MISMATCH')
                def observation(text):
                    return dict(text=text, fact_refs=wire.fact_refs, citation_refs=wire.citation_refs)
                analysis = dict(pair_id=wire.pair_id, category=packet.facts['category'],
                    citations=[c.model_dump(mode='json') for c in packet.citations],
                    established=[dict(text=('В заявке указан диагноз: ' + packet.facts['diagnosis']
                        + '; МНН покупки: ' + (packet.facts['inn'] or 'не установлено')
                        + '; исходная категория: ' + (packet.facts['category'] or 'не установлена')
                        + '; статус ML-2: ' + packet.facts['ml2_status'] + '.'),
                        fact_refs=['diagnosis', 'inn', 'category', 'ml2_status'], citation_refs=[])],
                    uncertain=[observation(wire.uncertain)],
                    questions=[dict(observation(wire.question), why=wire.why)])
                return dict(schema_version='staff-analysis.2', recommendations=[analysis])
            return raw
        except httpx.TimeoutException:
            raise ProviderError("GIGACHAT_TIMEOUT") from None
        except httpx.HTTPError as error:
            if _certificate_failure(error):
                raise ProviderError("GIGACHAT_TLS_ERROR") from None
            raise ProviderError("GIGACHAT_UNAVAILABLE") from None
        except ResponseError as error:
            codes = {
                400: "GIGACHAT_BAD_REQUEST", 401: "GIGACHAT_AUTH_FAILED",
                403: "GIGACHAT_FORBIDDEN", 404: "GIGACHAT_NOT_FOUND",
                422: "GIGACHAT_BAD_REQUEST", 429: "GIGACHAT_RATE_LIMIT",
            }
            code = "GIGACHAT_SERVER_ERROR" if error.status_code >= 500 else codes.get(error.status_code, "GIGACHAT_API_ERROR")
            raise ProviderError(code) from None
        except GigaChatException:
            raise ProviderError("GIGACHAT_API_ERROR") from None
        except (ValidationError, ValueError, UnicodeError):
            raise ProviderError("GIGACHAT_INVALID_RESPONSE") from None
