import base64
from copy import deepcopy
import json
import ssl
from types import SimpleNamespace
from uuid import UUID

from gigachat import GigaChat
from gigachat.models import Chat
import httpx
import jsonschema
import pytest

from llm_rag.contracts import AnalysisRequest
from llm_rag.corpus import Corpus
from llm_rag.providers import ProviderError
from tests.helpers import corpus_data, request_data
from tests.test_generation import generate


AUTH_KEY = base64.b64encode(b"test-client:test-secret").decode()
NOW = 1_800_000_000


@pytest.fixture
def sdk_wire(monkeypatch):
    """Подменяем только сетевой транспорт, оставляя настоящий SDK в работе."""
    import gigachat.client
    state = SimpleNamespace(handler=None, requests=[], now=NOW)
    monkeypatch.setattr(gigachat.client, "time", SimpleNamespace(time=lambda: state.now))

    def handle_request(transport, request):
        state.requests.append(request)
        assert state.handler is not None, "Сетевой ответ должен быть явно задан тестом"
        return state.handler(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    return state


def completion(raw, finish="stop"):
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                         "finish_reason": finish}], "model": "GigaChat-2:demo", "created": NOW,
            "object": "chat.completion", "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}}


def make_provider(**kwargs):
    from llm_rag.providers.gigachat import GigaChatProvider
    return GigaChatProvider(credentials=AUTH_KEY, enabled=True, **kwargs)


def test_real_sdk_receives_typed_chat_and_manages_oauth(sdk_wire, monkeypatch):
    raw, _, _, _, payload = generate()
    calls = []
    sdk_chat = GigaChat.chat

    def spy(client, request):
        calls.append(request)
        return sdk_chat(client, request)

    monkeypatch.setattr(GigaChat, "chat", spy)

    def handler(request):
        if request.url.path == "/api/v2/oauth":
            return httpx.Response(200, json={"access_token": "test-access", "expires_at": (NOW + 1800) * 1000})
        return httpx.Response(200, json=completion(raw))

    sdk_wire.handler = handler
    client = make_provider()
    assert client.generate(payload) == raw
    assert len(calls) == 1
    assert isinstance(calls[0], Chat)
    auth, chat = sdk_wire.requests
    assert auth.headers["Authorization"] == f"Basic {AUTH_KEY}"
    assert UUID(auth.headers["RqUID"]).version == 4
    assert auth.content == b"scope=GIGACHAT_API_PERS"
    assert str(chat.url) == "https://api.giga.chat/v1/chat/completions"
    assert chat.headers["Authorization"] == "Bearer test-access"
    body = json.loads(chat.content)
    assert body["model"] == "GigaChat-2"
    assert body["function_call"] == "none"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    context = json.loads(body["messages"][1]["content"])
    assert context["item"]["item_id"] == "row-1"
    assert "instructions" not in context
    assert context["evidence"][0]["claim_id"] == "category-a"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["strict"] is True
    assert set(body["response_format"]["schema"]["required"]) == {"item_id", "claims"}
    assert body["response_format"]["schema"]["properties"]["item_id"]["enum"] == ["row-1"]
    assert AUTH_KEY not in chat.content.decode()
    assert "test-access" not in chat.content.decode()
    assert all("embedding" not in str(r.url) for r in sdk_wire.requests)


def test_sdk_client_is_reused_across_items_and_refreshes_expired_token(sdk_wire):
    raw, _, _, _, payload = generate()

    def handler(request):
        if request.url.path == "/api/v2/oauth":
            return httpx.Response(200, json={"access_token": "token", "expires_at": (sdk_wire.now + 1800) * 1000})
        return httpx.Response(200, json=completion(raw))

    sdk_wire.handler = handler
    client = make_provider()
    client.generate(payload)
    sdk_wire.now += 100
    client.generate(payload)
    assert len([r for r in sdk_wire.requests if r.url.path == "/api/v2/oauth"]) == 1
    sdk_wire.now += 1700
    client.generate(payload)
    assert len([r for r in sdk_wire.requests if r.url.path == "/api/v2/oauth"]) == 2


def test_access_token_mode_and_grounding_validation(sdk_wire):
    from llm_rag.pipeline import Pipeline
    from llm_rag.providers.gigachat import GigaChatProvider

    def handler(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        evidence = context["evidence"][0]
        raw = {"item_id": context["item"]["item_id"], "claims": [{
            "claim_id": evidence["claim_id"], "fragment_id": evidence["fragment_id"],
            "text": "Выдуманный медицинский вывод", "quote": evidence["quote"],
        }]}
        return httpx.Response(200, json=completion(raw))

    sdk_wire.handler = handler
    client = GigaChatProvider(access_token="direct-token", enabled=True, model="GigaChat-2-Pro")
    result = Pipeline(Corpus.model_validate(corpus_data()), client).run(AnalysisRequest.model_validate(request_data()))
    assert len(sdk_wire.requests) == 1
    assert sdk_wire.requests[0].headers["Authorization"] == "Bearer direct-token"
    assert json.loads(sdk_wire.requests[0].content)["model"] == "GigaChat-2-Pro"
    assert result.items[0].explanation_status == "UNCONFIRMED"
    assert result.items[0].claims == []
    assert "Выдуманный" not in result.summary_text


def test_sdk_refresh_on_cached_401_is_bounded(sdk_wire):
    raw, _, _, _, payload = generate()
    chats = []

    def handler(request):
        if request.url.path == "/api/v2/oauth":
            return httpx.Response(200, json={"access_token": "token", "expires_at": (NOW + 1800) * 1000})
        chats.append(request)
        return httpx.Response(200, json=completion(raw)) if len(chats) == 1 else httpx.Response(401, text="private-secret")

    sdk_wire.handler = handler
    client = make_provider()
    client.generate(payload)
    with pytest.raises(ProviderError) as caught:
        client.generate(payload)
    assert [r.url.path for r in sdk_wire.requests] == ["/api/v2/oauth", "/v1/chat/completions", "/v1/chat/completions", "/api/v2/oauth", "/v1/chat/completions"]
    assert "private-secret" not in str(caught.value)


@pytest.mark.parametrize("kind", ["oauth_bad", "oauth_denied", "timeout", "rate_limit", "bad_json", "bad_inner_json", "truncated", "refusal", "function", "oversize", "redirect", "duplicate_keys"])
def test_sdk_and_response_failures_are_sanitized(sdk_wire, kind):
    raw, _, _, _, payload = generate()

    def handler(request):
        if request.url.path == "/api/v2/oauth":
            if kind == "oauth_bad":
                return httpx.Response(200, json={"access_token": "private-secret"})
            if kind == "oauth_denied":
                return httpx.Response(401, text="private-secret")
            return httpx.Response(200, json={"access_token": "private-secret", "expires_at": (NOW + 1800) * 1000})
        if kind == "timeout":
            raise httpx.ReadTimeout("private-secret", request=request)
        if kind == "rate_limit":
            return httpx.Response(429, text="private-secret")
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://another.example"})
        if kind == "bad_json":
            return httpx.Response(200, text="private-secret")
        if kind in {"truncated", "refusal", "function"}:
            return httpx.Response(200, json=completion(raw, {"truncated": "length", "refusal": "blacklist", "function": "function_call"}[kind]))
        result = completion(raw)
        if kind == "duplicate_keys":
            result["choices"][0]["message"]["content"] = '{"item_id":"wrong","item_id":"row-1","claims":[]}'
        else:
            result["choices"][0]["message"]["content"] = "x" * 1_000_001 if kind == "oversize" else "private-secret"
        return httpx.Response(200, json=result)

    sdk_wire.handler = handler
    with pytest.raises(ProviderError) as caught:
        make_provider().generate(payload)
    assert "private-secret" not in str(caught.value)


@pytest.mark.parametrize("kwargs", [
    {}, {"credentials": "key"}, {"credentials": "key", "enabled": True, "access_token": "token"},
    {"credentials": "key", "enabled": True, "scope": "invalid"},
    {"credentials": "key", "enabled": True, "timeout": float("nan")},
    {"credentials": "key", "enabled": True, "model": " "},
    {"credentials": "key\nInjected: yes", "enabled": True},
])
def test_configuration_is_checked_before_sdk_calls(sdk_wire, kwargs):
    from llm_rag.providers.gigachat import GigaChatProvider
    with pytest.raises(ValueError):
        GigaChatProvider(**kwargs)
    assert sdk_wire.requests == []


def test_non_demo_data_cannot_trigger_sdk_auth(sdk_wire):
    client = make_provider()
    with pytest.raises(ProviderError):
        client.generate({"is_demo": False})
    assert sdk_wire.requests == []


def test_sdk_is_configured_with_tls_and_closed_through_provider(monkeypatch, sdk_wire):
    from llm_rag.providers.gigachat import GigaChatProvider
    raw, _, _, _, payload = generate()
    sdk_wire.handler = lambda request: httpx.Response(200, json=completion(raw))
    original_init, original_close = GigaChat.__init__, GigaChat.close
    settings, closed = [], []

    def init(client, **kwargs):
        settings.append(kwargs)
        original_init(client, **kwargs)

    def close(client):
        closed.append(client)
        original_close(client)

    monkeypatch.setattr(GigaChat, "__init__", init)
    monkeypatch.setattr(GigaChat, "close", close)
    with GigaChatProvider(access_token="direct-token", enabled=True) as client:
        assert client.generate(payload) == raw
    assert settings[0]["verify_ssl_certs"] is True
    assert settings[0]["max_retries"] == 0
    assert len(closed) == 1


def test_sdk_tls_verification_can_be_disabled_explicitly(monkeypatch):
    settings = []
    original_init = GigaChat.__init__

    def init(client, **kwargs):
        settings.append(kwargs)
        original_init(client, **kwargs)

    monkeypatch.setattr(GigaChat, "__init__", init)
    with make_provider(verify_ssl_certs=False):
        assert settings[0]["verify_ssl_certs"] is False
        assert settings[0]["ssl_context"] is None


def test_sdk_environment_cannot_disable_verification_by_default(monkeypatch):
    monkeypatch.setenv("GIGACHAT_VERIFY_SSL_CERTS", "false")
    with make_provider() as provider:
        assert provider._client._settings.verify_ssl_certs is True


def test_certificate_failure_has_specific_sanitized_code(sdk_wire):
    _, _, _, _, payload = generate()

    def handler(request):
        try:
            raise ssl.SSLCertVerificationError(1, "private-secret")
        except ssl.SSLCertVerificationError as cause:
            raise httpx.ConnectError("private-secret", request=request) from cause

    sdk_wire.handler = handler
    with pytest.raises(ProviderError, match="^GIGACHAT_TLS_ERROR$"):
        make_provider().generate(payload)


@pytest.mark.parametrize("status,code", [
    (400, "GIGACHAT_BAD_REQUEST"), (401, "GIGACHAT_AUTH_FAILED"),
    (403, "GIGACHAT_FORBIDDEN"), (404, "GIGACHAT_NOT_FOUND"),
    (429, "GIGACHAT_RATE_LIMIT"), (503, "GIGACHAT_SERVER_ERROR"),
])
def test_http_failure_has_specific_sanitized_code(sdk_wire, status, code):
    _, _, _, _, payload = generate()
    sdk_wire.handler = lambda request: httpx.Response(status, text="private-secret")
    with pytest.raises(ProviderError) as caught:
        make_provider().generate(payload)
    assert str(caught.value) == code


@pytest.mark.parametrize("with_dosage", [False, True])
def test_generation_schema_limits_output_to_complete_allowed_claims(sdk_wire, with_dosage):
    raw, _, _, _, payload = generate(with_dosage=with_dosage)

    def handler(request):
        if request.url.path == "/api/v2/oauth":
            return httpx.Response(200, json={"access_token": "token", "expires_at": (NOW + 1800) * 1000})
        return httpx.Response(200, json=completion(raw))

    sdk_wire.handler = handler
    make_provider().generate(payload)
    schema = json.loads(sdk_wire.requests[-1].content)["response_format"]["schema"]
    jsonschema.validate(raw, schema)
    for field in ("claim_id", "text", "fragment_id", "quote"):
        wrong = deepcopy(raw)
        wrong["claims"][0][field] = "x"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(wrong, schema)
    missing = deepcopy(raw)
    missing["claims"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing, schema)
    repeated = deepcopy(raw)
    repeated["claims"] = [raw["claims"][0]] * (len(raw["claims"]) + 1)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(repeated, schema)
    if with_dosage:
        mixed = deepcopy(raw)
        mixed["claims"][0]["quote"] = raw["claims"][1]["quote"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(mixed, schema)
