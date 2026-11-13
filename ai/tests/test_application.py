from pathlib import Path

import pytest

from llm_rag.providers import DemoProvider


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def configured_provider(monkeypatch, tmp_path):
    """Используем выдуманный ключ и локальный генератор вместо сетевого SDK."""
    import llm_rag.application as application

    for key in ("GIGACHAT_CREDENTIALS", "GIGACHAT_ACCESS_TOKEN", "GIGACHAT_MODEL",
                "GIGACHAT_SCOPE", "GIGACHAT_CA_BUNDLE_FILE"):
        monkeypatch.delenv(key, raising=False)
    settings, closed = [], []

    class LocalProvider(DemoProvider):
        name = "test-gigachat"

        def __init__(self, **kwargs):
            settings.append(kwargs)

        def close(self):
            closed.append(self)

    monkeypatch.setattr(application, "GigaChatProvider", LocalProvider)
    env_file = tmp_path / ".env"
    env_file.write_text("GIGACHAT_CREDENTIALS=fake-file-key\nGIGACHAT_MODEL=TestModel\n", encoding="utf-8-sig")
    return application, env_file, settings, closed


def test_env_file_configures_existing_pipeline_without_exposing_key(configured_provider):
    application, env_file, settings, closed = configured_provider
    result = application.analyze_files(
        ROOT / "examples/request.json", ROOT / "examples/corpus.json",
        provider_name="gigachat", enable_api=True, env_file=env_file,
    )
    assert settings[0]["credentials"] == "fake-file-key"
    assert settings[0]["model"] == "TestModel"
    assert settings[0]["verify_ssl_certs"] is True
    assert result.provider_name == "test-gigachat"
    assert len(result.items) == 8
    assert str(result.costs_by_currency[0].total_amount) == "355.00"
    assert "fake-file-key" not in result.model_dump_json()
    assert len(closed) == 1


def test_environment_has_priority_over_env_file(configured_provider, monkeypatch):
    application, env_file, settings, _ = configured_provider
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "fake-terminal-key")
    application.analyze_files(
        ROOT / "examples/request.json", ROOT / "examples/corpus.json",
        provider_name="gigachat", enable_api=True, env_file=env_file,
    )
    assert settings[0]["credentials"] == "fake-terminal-key"


def test_sdk_is_closed_when_pipeline_cannot_be_created(configured_provider, monkeypatch):
    application, env_file, _, closed = configured_provider

    def fail(*args, **kwargs):
        raise ValueError("Тестовый отказ конвейера")

    monkeypatch.setattr(application, "Pipeline", fail)
    with pytest.raises(ValueError):
        application.analyze_files(
            ROOT / "examples/request.json", ROOT / "examples/corpus.json",
            provider_name="gigachat", enable_api=True, env_file=env_file,
        )
    assert len(closed) == 1


def test_api_is_not_enabled_by_key_in_env_file_alone(configured_provider):
    application, env_file, settings, _ = configured_provider
    with pytest.raises(ValueError):
        application.analyze_files(
            ROOT / "examples/request.json", ROOT / "examples/corpus.json",
            provider_name="gigachat", env_file=env_file,
        )
    assert settings == []


def test_cli_uses_env_file_and_shared_analysis_function(configured_provider, tmp_path):
    from llm_rag.__main__ import main
    _, env_file, settings, closed = configured_provider
    output = tmp_path / "result.json"
    code = main([
        "--provider", "gigachat", "--enable-api", "--env-file", str(env_file),
        "--request", str(ROOT / "examples/request.json"),
        "--corpus", str(ROOT / "examples/corpus.json"), "--output", str(output),
    ])
    assert code == 0
    assert settings[0]["credentials"] == "fake-file-key"
    assert output.exists()
    assert len(closed) == 1


def test_local_tls_option_is_forwarded_explicitly(configured_provider):
    application, env_file, settings, _ = configured_provider
    application.analyze_files(
        ROOT / "examples/request.json", ROOT / "examples/corpus.json",
        provider_name="gigachat", enable_api=True, env_file=env_file, verify_ssl_certs=False,
    )
    assert settings[0]["verify_ssl_certs"] is False
