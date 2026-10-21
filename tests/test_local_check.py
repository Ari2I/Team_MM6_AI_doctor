import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("choice,provider,retrieval", [
    ("1", "demo", "lexical"), ("2", "demo", "vector"),
    ("3", "gigachat", "lexical"), ("4", "gigachat", "vector"),
])
def test_menu_calls_shared_function_and_saves_result(monkeypatch, tmp_path, capsys, choice, provider, retrieval):
    import check_local
    from llm_rag.application import analyze_files

    result = analyze_files(ROOT / "examples/request.json", ROOT / "examples/corpus.json", env_file=tmp_path / "absent.env")
    calls = []

    def analyze(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    answers = iter([choice, "0"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(check_local, "analyze_files", analyze)
    monkeypatch.setattr(check_local, "PROJECT_ROOT", tmp_path)
    assert check_local.main() == 0
    assert len(calls) == 1
    assert calls[0][1]["provider_name"] == provider
    assert calls[0][1]["retrieval"] == retrieval
    assert calls[0][1]["enable_api"] is (provider == "gigachat")
    assert calls[0][1]["verify_ssl_certs"] is True
    outputs = list((tmp_path / ".local/results").glob("*.json"))
    assert len(outputs) == 1
    assert len(json.loads(outputs[0].read_text(encoding="utf-8"))["items"]) == 8
    assert "355.00" in capsys.readouterr().out


def test_menu_handles_invalid_choice_and_configuration_error(monkeypatch, capsys):
    import check_local

    def fail(*args, **kwargs):
        raise ValueError("private-secret")

    answers = iter(["неверно", "3", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(check_local, "analyze_files", fail)
    assert check_local.main() == 0
    output = capsys.readouterr().out
    assert "private-secret" not in output
    assert "Ошибка" in output


def test_standalone_script_runs_real_demo_from_another_directory(tmp_path):
    process = subprocess.run(
        [sys.executable, str(ROOT / "check_local.py")], input="1\n0\n", cwd=tmp_path,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert process.returncode == 0, process.stderr
    assert "offline-extractive-demo" in process.stdout
    assert "355.00" in process.stdout
    assert "SUPPORTED" in process.stdout


def test_menu_explains_tls_failure_in_russian(tmp_path, capsys):
    import check_local
    from tests.test_pipeline import run
    from llm_rag.providers import ProviderError

    class Failure:
        name = "gigachat-api"

        def generate(self, payload):
            raise ProviderError("GIGACHAT_TLS_ERROR")

    check_local._show_result(run(provider=Failure()), tmp_path / "result.json")
    output = capsys.readouterr().out
    assert "GIGACHAT_TLS_ERROR" in output
    assert "сертификат" in output.lower()


def test_local_menu_can_disable_tls_verification_for_current_session(monkeypatch, tmp_path, capsys):
    import check_local
    from tests.test_pipeline import run
    calls = []

    def analyze(*args, **kwargs):
        calls.append(kwargs)
        return run()

    answers = iter(["7", "4", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(check_local, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(check_local, "analyze_files", analyze)
    assert check_local.main() == 0
    assert len(calls) == 1
    assert calls[0]["verify_ssl_certs"] is False
    assert "Проверка сертификата: выключена" in capsys.readouterr().out
