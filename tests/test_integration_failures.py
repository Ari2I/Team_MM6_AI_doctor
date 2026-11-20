"""Клиент создаётся только по opt-in; ошибка создания даёт полезный резерв."""

from tests.test_integration import request


def test_failed_provider_creation_is_explicit_fallback():
    from llm_rag.integration import _run, IntegrationResources, IntegrationOptions

    def factory():
        raise OSError("private-token")

    result = _run(
        request(),
        resources=IntegrationResources(provider_factory=factory),
        options=IntegrationOptions(),
    )
    assert all(
        "AI_UNAVAILABLE" in pair.reasons and pair.generation_mode == "FALLBACK" for pair in result.pairs
    )
    assert "private-token" not in result.model_dump_json()
