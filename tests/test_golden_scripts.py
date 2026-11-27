"""Стабильные снимки и безопасная диагностика без настоящего HTTP-сервера."""

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import capture_golden, compare_golden, golden_support
from scripts.golden_support import CASES, GoldenError, differences, normalize, normalized_card

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def card() -> dict[str, Any]:
    value = json.loads(Path("sample_api_response.json").read_text(encoding="utf-8"))
    value["application"].update(
        status="analyzed", reference_is_synthetic=True, reference_name="Синтетический тест"
    )
    for item in value["items"]:
        for check in item["checks"]:
            check["reference_version"] = "demo-api-2"
    return value


def test_normalization_removes_volatility_preserves_meaning(card: dict[str, Any]) -> None:
    other = copy.deepcopy(card)
    other["application"].update(
        id="00000000-0000-0000-0000-000000000001", created_at="2026-01-01T00:00:00Z"
    )
    other["items"].reverse()
    other["diagnoses"].reverse()
    for item in other["items"]:
        item["checks"].reverse()
    assert normalized_card(card) == normalized_card(other)
    other["items"][0]["amount"] = "999.00"
    assert normalized_card(card) != normalized_card(other)
    assert normalize({"id": "hidden", "processing_seconds": 1, "amount": "12.30"}) == {
        "amount": "12.30"
    }


@pytest.mark.parametrize("synthetic,version", [(False, "demo-api-2"), (True, "unconfigured")])
def test_refuses_non_demo_or_unconfigured_reference(
    card: dict[str, Any], synthetic: bool, version: str
) -> None:
    card["application"]["reference_is_synthetic"] = synthetic
    card["items"][0]["checks"][0]["reference_version"] = version
    with pytest.raises(GoldenError):
        normalized_card(card)


def test_all_four_uploads_and_saved_cards_match(card: dict[str, Any]) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path in {"/applications", f"/applications/{card['application']['id']}"}
        return httpx.Response(201 if request.method == "POST" else 200, json=card)

    with httpx.Client(base_url="http://test/", transport=httpx.MockTransport(respond)) as client:
        cards = golden_support.fetch_cards(client)
    assert set(cards) == set(CASES)
    assert [request.method for request in requests] == ["POST", "GET"] * 4
    for case, request in zip(CASES, requests[::2], strict=True):
        assert f'filename="{case}.xlsx"'.encode() in request.content


def test_output_is_redacted_even_for_unknown_keys() -> None:
    left = {"comment": "private-left", "private-key": "private", "category": "А"}
    right = {"comment": "private-right", "private-key": "other", "category": "Б"}
    output = "\n".join(differences(left, right))
    assert "private" not in output and "other" not in output
    assert "А → Б" in output and "comment" in output
    assert "AI_TIMEOUT → AI_UNAVAILABLE" in "\n".join(
        differences({"review_reason": "AI_TIMEOUT"}, {"review_reason": "AI_UNAVAILABLE"})
    )


def test_capture_compare_and_no_accidental_overwrite(
    card: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cards = {case: normalized_card(card) for case in CASES}
    monkeypatch.setattr(capture_golden, "capture_from_stack", lambda: cards)
    assert capture_golden.main(["--output-dir", str(tmp_path)]) == 0
    assert capture_golden.main(["--output-dir", str(tmp_path)]) == 2
    assert compare_golden.main(["--golden-dir", str(tmp_path), "--current-dir", str(tmp_path)]) == 0
    current = tmp_path / "current"
    current.mkdir()
    for case in CASES:
        changed = copy.deepcopy(cards[case])
        changed["application"]["summary"] = "private-card-text"
        (current / f"{case}.json").write_text(json.dumps(changed), encoding="utf-8")
    assert compare_golden.main(["--golden-dir", str(tmp_path), "--current-dir", str(current)]) == 1
    assert "private-card-text" not in capsys.readouterr().out


def test_capture_errors_do_not_expose_response_or_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def failed() -> Any:
        raise RuntimeError("private-credentials-and-response")

    monkeypatch.setattr(capture_golden, "capture_from_stack", failed)
    assert capture_golden.main(["--output-dir", str(tmp_path)]) == 2
    assert "private" not in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_live_client_tls_no_redirects_and_no_host_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = {}

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            configured.update(kwargs)

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr(
        golden_support.os,
        "environ",
        {"WEB_URL": "https://test.invalid", "WEB_USER": "test", "WEB_PASSWORD": "test"},
    )
    monkeypatch.setattr(golden_support.httpx, "Client", Client)
    monkeypatch.setattr(golden_support, "fetch_cards", lambda _: {})
    golden_support.capture_from_stack()
    assert configured["verify"] is True
    assert configured["follow_redirects"] is False and configured["trust_env"] is False


@pytest.mark.parametrize("url", ["http://remote.invalid", "https://user:password@test.invalid"])
def test_disallows_remote_plaintext_or_credentials_in_url(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(
        golden_support.os, "environ", {"WEB_URL": url, "WEB_USER": "test", "WEB_PASSWORD": "test"}
    )
    with pytest.raises(GoldenError):
        golden_support.capture_from_stack()
