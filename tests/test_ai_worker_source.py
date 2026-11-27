"""Actual worker loader and public facade import, with no provider/network call."""

from pathlib import Path

import pytest

from scripts import check_ai_worker_source as check

pytestmark = pytest.mark.usefixtures("no_network")


def test_probe_resolves_actual_worker_source_and_tls():
    if not Path("ai/src").is_dir():
        pytest.skip("AI library is absent")
    result = check.probe()
    assert result["matches_repository_source"]
    assert result["tls_verification"] is True
    assert result["network_attempts"] == 0
    assert Path(result["integration_file"]) == (check.ROOT / "ai/src/llm_rag/integration.py")
    assert set(result["files"]) == set(check.SOURCE_FILES)


def test_probe_failure_does_not_print_child_output(monkeypatch, capsys):
    def failed():
        raise RuntimeError("PRIVATE-CHILD-OUTPUT")

    monkeypatch.setattr(check, "probe", failed)
    assert check.main() == 1
    assert capsys.readouterr().out == "AI_WORKER_PROBE_FAILED: RuntimeError\n"
