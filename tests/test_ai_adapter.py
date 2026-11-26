"""Контракт адаптера с подменённым AI и независимые проверки бюджета/изоляции."""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import REVIEW_REASON_LABELS, AISettings
from app.models.enums import DrugCategory, ReviewReason
from app.schemas.analysis import PairCheck
from app.services import ai_adapter, ai_worker
from app.services.ai_adapter import AIAdapter, AIInput, backend_category, module_category

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def pair() -> AIInput:
    return AIInput(
        drug_name="Демо",
        mnn="демо-вещество-а",
        diagnosis="Демо-диагноз",
        dosage="5 мг",
        quantity=Decimal(2),
        amount=Decimal("900"),
    )


@pytest.fixture
def local() -> PairCheck:
    return PairCheck(category="А", source="Локальное правило", reference_version="demo-api-2")


@pytest.fixture
def fake_module(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Подмена именно интерфейса llm_rag; тяжёлые зависимости не нужны."""
    state = SimpleNamespace(status="SUPPORTED", error=None, calls=0, changed=False, request=None)
    assertion = SimpleNamespace(
        category="А", upstream_reason="Разрешённая причина", kind="CATEGORY"
    )
    corpus = SimpleNamespace(
        corpus_version="corpus-test",
        fragments=[
            SimpleNamespace(
                inn="демо-вещество-а",
                diagnosis="Демо-диагноз",
                required_context={},
                assertions=[assertion],
            )
        ],
    )

    class Request:
        @staticmethod
        def model_validate(data: dict[str, Any]) -> Any:
            item = SimpleNamespace(**data["items"][0])
            state.request = SimpleNamespace(**{**data, "items": [item]})
            return state.request

    class Pipeline:
        def __init__(self, corpus: Any, provider: Any, *, embedder: Any) -> None:
            assert embedder is None

        def run(self, request: Any) -> Any:
            state.calls += 1
            if state.error:
                raise state.error
            if getattr(state, "malformed", False):
                return None
            item = SimpleNamespace(**vars(request.items[0]))
            if state.changed:
                item.category = "В"
            item.explanation_status = state.status
            item.comment = "Учебное пояснение"
            item.claims = [
                SimpleNamespace(
                    citations=[
                        SimpleNamespace(
                            title="Синтетический источник",
                            source_id="s",
                            fragment_id="f",
                            version="v",
                        )
                    ]
                )
            ]
            item.reasons = state.reasons if hasattr(state, "reasons") else []
            result = SimpleNamespace(
                request_id=request.request_id,
                diagnosis=request.diagnosis,
                items=[item],
                corpus_version=corpus.corpus_version,
            )
            result.model_dump = lambda: result
            return result

    modules = {
        "llm_rag.contracts": SimpleNamespace(
            AnalysisRequest=Request,
            AnalysisResult=SimpleNamespace(model_validate=lambda r: r),
        ),
        "llm_rag.corpus": SimpleNamespace(
            Corpus=SimpleNamespace(model_validate_json=lambda _: corpus),
            normalize=lambda text: text.casefold().strip(),
        ),
        "llm_rag.pipeline": SimpleNamespace(Pipeline=Pipeline),
        "llm_rag.providers.demo": SimpleNamespace(DemoProvider=lambda: object()),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def run(*args: Any, **kwargs: Any) -> Any:
        result = ai_worker.execute(json.loads(kwargs["input"]))
        return SimpleNamespace(stdout=result.model_dump_json())

    monkeypatch.setattr(ai_adapter.subprocess, "run", run)
    return state


def adapter(**kwargs: Any) -> AIAdapter:
    return AIAdapter(AISettings(mode="offline", corpus_path="ai/examples/corpus.json", **kwargs))


def test_template_never_imports_or_starts_module(
    monkeypatch: pytest.MonkeyPatch, pair: AIInput, local: PairCheck
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Template tried to start AI")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    assert AIAdapter(AISettings()).analyze(pair, local) is local


def test_success_keeps_local_rule(
    fake_module: SimpleNamespace, pair: AIInput, local: PairCheck
) -> None:
    result = adapter().analyze(pair, local)
    assert result.category == local.category
    assert result.source == local.source and result.reference_version == local.reference_version
    assert result.comment == "Учебное пояснение" and not result.needs_review
    assert fake_module.request.items[0].dosage_check["status"] == "NOT_CHECKED"
    assert fake_module.calls == 1


def test_preliminary_category_always_needs_review(
    fake_module: SimpleNamespace, pair: AIInput
) -> None:
    result = adapter().analyze(pair, PairCheck(needs_review=True, review_reason="NO_RULE_FOR_PAIR"))
    assert result.category == "А" and result.needs_review
    assert result.review_reason == ReviewReason.AI_PRELIMINARY
    assert result.reference_version == "corpus-test" and result.source


@pytest.mark.parametrize(
    "field,value",
    [
        ("dosage", None),
        ("dosage", " "),
        ("quantity", None),
        ("quantity", Decimal("1.5")),
        ("quantity", Decimal(0)),
        ("amount", None),
        ("amount", Decimal("NaN")),
    ],
)
def test_incomplete_not_called(
    fake_module: SimpleNamespace, pair: AIInput, local: PairCheck, field: str, value: Any
) -> None:
    result = adapter().analyze(pair.model_copy(update={field: value}), local)
    assert result.category == local.category and result.needs_review
    assert result.review_reason == ReviewReason.AI_INPUT_INCOMPLETE
    assert fake_module.calls == 0


@pytest.mark.parametrize(
    "error,reason",
    [
        (RuntimeError("private request or SDK details"), "AI_UNAVAILABLE"),
        (TimeoutError(), "AI_TIMEOUT"),
        (ValueError("bad response"), "AI_INVALID_RESPONSE"),
    ],
)
def test_errors_are_safe_and_next_pair_continues(
    fake_module: SimpleNamespace, pair: AIInput, local: PairCheck, error: Exception, reason: str
) -> None:
    fake_module.error = error
    service = adapter()
    failed = service.analyze(pair, local)
    assert failed.review_reason == reason and failed.needs_review
    assert failed.category == local.category and failed.source == local.source
    assert "private" not in failed.model_dump_json()
    fake_module.error = None
    assert not service.analyze(pair, local).needs_review


def test_mutation_of_input_is_invalid(
    fake_module: SimpleNamespace, pair: AIInput, local: PairCheck
) -> None:
    fake_module.changed = True
    result = adapter().analyze(pair, local)
    assert result.review_reason == "AI_INVALID_RESPONSE" and result.category == "А"


def test_malformed_module_response(
    fake_module: SimpleNamespace, pair: AIInput, local: PairCheck
) -> None:
    fake_module.malformed = True
    assert adapter().analyze(pair, local).review_reason == "AI_INVALID_RESPONSE"


@pytest.mark.parametrize(
    "reasons,code",
    [
        (["GIGACHAT_TIMEOUT"], "AI_TIMEOUT"),
        (["GIGACHAT_INVALID_RESPONSE"], "AI_INVALID_RESPONSE"),
        (["ITEM_PROCESSING_ERROR"], "AI_UNAVAILABLE"),
    ],
)
def test_pipeline_swallowed_errors(
    fake_module: SimpleNamespace, pair: AIInput, local: PairCheck, reasons: list[str], code: str
) -> None:
    fake_module.status = "PROCESSING_ERROR"
    fake_module.reasons = reasons
    assert adapter().analyze(pair, local).review_reason == code


@pytest.mark.parametrize("value", ["not json", "{}", '{"check":{"category":"X"}}'])
def test_invalid_worker_response(
    monkeypatch: pytest.MonkeyPatch, pair: AIInput, local: PairCheck, value: str
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=value))
    assert adapter().analyze(pair, local).review_reason == "AI_INVALID_RESPONSE"


def test_pair_timeout_kills_process(
    monkeypatch: pytest.MonkeyPatch, pair: AIInput, local: PairCheck
) -> None:
    real_run = subprocess.run

    def blocked(*args: Any, **kwargs: Any) -> Any:
        return real_run([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)

    monkeypatch.setattr(subprocess, "run", blocked)
    assert adapter(pair_timeout_seconds=0.1).analyze(pair, local).review_reason == "AI_TIMEOUT"


def test_application_deadline_skips_remaining_pairs(
    fake_module: SimpleNamespace, pair: AIInput, local: PairCheck
) -> None:
    service = adapter()
    service.deadline = -1
    for _ in range(3):
        assert service.analyze(pair, local).review_reason == "AI_TIMEOUT"
    assert fake_module.calls == 0


@pytest.mark.parametrize("cyrillic", ["А", "Б", "В"])
def test_category_mapping(cyrillic: str) -> None:
    assert backend_category(cyrillic) == cyrillic
    assert module_category(DrugCategory(cyrillic)) == cyrillic
    assert backend_category(None) is None
    with pytest.raises(ValueError):
        backend_category("C")


@pytest.mark.parametrize("latin", ["A", "B", "V"])
def test_latin_categories_are_rejected(latin: str) -> None:
    with pytest.raises(ValueError):
        backend_category(latin)


def test_offline_guard() -> None:
    for event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
        with pytest.raises(PermissionError):
            ai_worker._deny_network(event, ())


def test_offline_guard_blocks_socket_before_connection() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import socket
import sys
from app.services.ai_worker import _deny_network
sys.addaudithook(_deny_network)
try:
    socket.create_connection(('127.0.0.1', 1))
except PermissionError:
    print('blocked')
else:
    raise AssertionError('Offline guard did not reject socket')
""",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert completed.stdout.strip() == "blocked"


def test_network_allowlist_excludes_private_fields() -> None:
    evidence = {key: key for key in ai_worker.EVIDENCE_FIELDS}
    evidence.update(kind="CATEGORY", required_context={}, unexpected="private")
    filtered = ai_worker.network_payload(
        {
            "corpus_version": "v",
            "request_id": "private",
            "context": {"person": "private"},
            "item": dict.fromkeys(ai_worker.NETWORK_ITEM_FIELDS, "demo")
            | {
                "original_dosage": "private",
                "amount": "private",
                "classification_reason": "private",
            },
            "evidence": [evidence],
        }
    )
    assert "private" not in json.dumps(filtered)
    assert set(filtered["item"]) == set(ai_worker.NETWORK_ITEM_FIELDS)
    assert "item.inn" in ai_worker.sent_field_names(filtered)


def test_ordinary_mode_needs_no_demo_flag(
    fake_module: SimpleNamespace, pair: AIInput, local: PairCheck
) -> None:
    result = AIAdapter(AISettings(mode="offline")).analyze(pair, local)
    assert result.category == local.category and not result.needs_review
    assert result.source == local.source and result.comment
    assert fake_module.calls == 0


def test_reason_storage_needs_no_enum_migration() -> None:
    from sqlalchemy import Text

    from app.models import ItemCheck

    assert isinstance(ItemCheck.__table__.c.review_reason.type, Text)
    assert all(reason.value in REVIEW_REASON_LABELS for reason in ReviewReason)


def test_insufficient_evidence_cannot_propose_category(
    fake_module: SimpleNamespace, pair: AIInput
) -> None:
    fake_module.status = "SOURCE_CONFLICT"
    result = adapter().analyze(pair, PairCheck(needs_review=True, review_reason="NO_RULE_FOR_PAIR"))
    assert result.category is None and result.needs_review


def test_remaining_application_budget_limits_next_worker(
    monkeypatch: pytest.MonkeyPatch, pair: AIInput, local: PairCheck
) -> None:
    service = adapter()
    now = ai_adapter.monotonic()
    service.deadline = now + 3
    monkeypatch.setattr(ai_adapter, "monotonic", lambda: now)

    def run(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["timeout"] == 3
        raise subprocess.TimeoutExpired(args[0], 3)

    monkeypatch.setattr(subprocess, "run", run)
    assert service.analyze(pair, local).review_reason == "AI_TIMEOUT"


def test_timeout_retains_only_safe_network_metadata(
    monkeypatch: pytest.MonkeyPatch, pair: AIInput, local: PairCheck
) -> None:
    metadata = ai_adapter.WorkerResult(sent_fields=["item.inn"], tokens=7)

    def run(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(
            args[0], 1, output=("AI_META " + metadata.model_dump_json() + "\n").encode()
        )

    monkeypatch.setattr(subprocess, "run", run)
    service = adapter()
    assert service.analyze(pair, local).review_reason == "AI_TIMEOUT"
    assert service.last_tokens == 7 and service.last_sent_fields == ["item.inn"]


def test_network_provider_requires_tls_with_mock_sdk(
    fake_module: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    pair: AIInput,
    local: PairCheck,
) -> None:
    configured = {}

    class Provider:
        usage_totals = {"total_tokens": 0}

        def __init__(self, **kwargs: Any) -> None:
            configured.update(kwargs)

        def close(self) -> None:
            configured["closed"] = True

    monkeypatch.setitem(
        sys.modules, "llm_rag.providers.gigachat", SimpleNamespace(GigaChatProvider=Provider)
    )
    monkeypatch.setattr(
        ai_worker,
        "AINetworkSettings",
        lambda: SimpleNamespace(
            credentials=SimpleNamespace(get_secret_value=lambda: None),
            scope="GIGACHAT_API_PERS",
            ca_bundle_file="/external/ca.pem",
        ),
    )
    service = AIAdapter(AISettings(mode="network", corpus_path="ai/examples/corpus.json"))
    result = service.analyze(pair, local)
    assert not result.needs_review
    assert configured["verify_ssl_certs"] is True
    assert configured["ca_bundle"] == "/external/ca.pem" and configured["closed"]


def test_real_offline_worker(pair: AIInput, local: PairCheck) -> None:
    if not Path("ai/src/llm_rag/pipeline.py").is_file():
        pytest.skip("В template-образе AI намеренно отсутствует")
    result = adapter().analyze(pair, local)
    assert result.category == "А" and result.source == local.source
    assert result.comment and not result.needs_review
    assert result.review_reason is None
