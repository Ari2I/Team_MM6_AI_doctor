"""Клиент альтернативного JSON-шлюза генерации."""

import json
import math
from urllib.parse import urlsplit

import httpx

from .base import ProviderError


class JsonApiProvider:
    """Отправка POST по протоколу нашего шлюза без привязки к API конкретного поставщика."""

    name = "https-json-gateway"

    def __init__(
        self, endpoint: str, api_key: str, *, enabled: bool = False,
        timeout: float = 20.0, transport: httpx.BaseTransport | None = None,
    ):
        url = urlsplit(endpoint)
        if not enabled or url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("Explicit enablement and an HTTPS endpoint without credentials are required")
        if not api_key.strip() or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("A key and a finite positive timeout are required")
        self._endpoint = endpoint
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    def generate(self, payload: dict) -> dict:
        if payload.get("is_demo") is not True:
            raise ProviderError("DEMO_DATA_REQUIRED")
        try:
            with httpx.Client(timeout=self._timeout, follow_redirects=False, trust_env=False,
                              transport=self._transport) as client:
                with client.stream("POST", self._endpoint, json=payload, headers={
                    "Authorization": f"Bearer {self._api_key}", "Accept": "application/json",
                }) as response:
                    if not 200 <= response.status_code < 300:
                        raise ProviderError("HTTP_ERROR")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 1_000_000:
                            raise ProviderError("RESPONSE_TOO_LARGE")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ProviderError("INVALID_JSON_OBJECT")
            return result
        except httpx.TimeoutException:
            raise ProviderError("API_TIMEOUT") from None
        except httpx.HTTPError:
            raise ProviderError("API_UNAVAILABLE") from None
        except (ValueError, UnicodeError):
            raise ProviderError("INVALID_JSON") from None
