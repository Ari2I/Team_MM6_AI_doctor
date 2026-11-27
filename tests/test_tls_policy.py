"""Все принадлежащие backend сетевые фабрики сохраняют TLS-проверку."""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import ml2_analysis, standard_analysis

pytestmark = pytest.mark.usefixtures("no_network")


def test_no_insecure_tls_clients_in_backend_or_scripts() -> None:
    clients = {"GigaChatProvider", "ML2GigaChatProvider", "Client", "AsyncClient"}
    checked = 0
    for root in (Path("app"), Path("scripts")):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                name = getattr(call.func, "attr", getattr(call.func, "id", ""))
                assert name != "_create_unverified_context", path
                keywords = {keyword.arg: keyword.value for keyword in call.keywords}
                for key in ("verify", "verify_ssl_certs", "check_hostname"):
                    if key in keywords:
                        assert isinstance(keywords[key], ast.Constant), (path, call.lineno, key)
                        assert keywords[key].value is True, (path, call.lineno, key)
                if name in clients:
                    checked += 1
                    required = "verify_ssl_certs" if "GigaChat" in name else "verify"
                    assert required in keywords, (path, call.lineno, required)
    assert checked >= 4


@pytest.mark.parametrize("factory", ["standard", "ml2"])
@pytest.mark.parametrize("scope", ["", "GIGACHAT_API_PERS", "GIGACHAT_API_B2B"])
@pytest.mark.parametrize("ca_bundle", ["", "/external/test-ca.pem"])
def test_network_factory_always_enables_tls(
    monkeypatch: pytest.MonkeyPatch, factory: str, scope: str, ca_bundle: str
) -> None:
    configured: dict[str, Any] = {}

    class Provider:
        def __init__(self, **kwargs: Any) -> None:
            configured.update(kwargs)

    service = standard_analysis if factory == "standard" else ml2_analysis
    module = "llm_rag.providers.gigachat" if factory == "standard" else "llm_rag.ml2.provider"
    symbol = "GigaChatProvider" if factory == "standard" else "ML2GigaChatProvider"
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(**{symbol: Provider}))
    monkeypatch.setitem(
        sys.modules, "llm_rag.network_policy", SimpleNamespace(NetworkPolicy=SimpleNamespace)
    )
    monkeypatch.setattr(
        service,
        "AINetworkSettings",
        lambda: SimpleNamespace(
            credentials=SimpleNamespace(get_secret_value=lambda: None),
            scope=scope,
            ca_bundle_file=ca_bundle,
        ),
    )
    service.create_provider(5)
    assert configured["verify_ssl_certs"] is True
    assert configured["ca_bundle"] == (ca_bundle or None)
