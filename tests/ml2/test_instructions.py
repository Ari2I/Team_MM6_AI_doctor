"""Проверки доступности общих инструкций вне исходной рабочей папки."""

import json
import re
from pathlib import Path

import pytest

from llm_rag.ml2 import load_classification_policy, policy
from llm_rag.ml2.classifier import Assessment, ClassificationRequest, prompt_for


def test_classifier_and_rag_read_the_same_policy_from_any_working_directory(
    tmp_path, monkeypatch
):
    instruction_dir = (
        Path(__file__).resolve().parents[2] / "src/llm_rag/ml2/instructions"
    )
    expected = (
        (instruction_dir / "classification_policy.md")
        .read_text(encoding="utf-8")
        .strip()
    )
    template = (
        (instruction_dir / "evidence_extraction.md").read_text(encoding="utf-8").strip()
    )
    monkeypatch.chdir(tmp_path)
    system, payload = prompt_for(
        ClassificationRequest("r", "i", "J45.9", "тестовое вещество", (), {})
    )
    rules = policy.load_classification_rules()
    assert rules.policy_version in load_classification_policy()
    assert (
        expected.replace("{{assessment_rules}}", "").splitlines()[0]
        in load_classification_policy()
    )
    assert system == template.replace(
        "{{classification_policy}}", load_classification_policy()
    )
    assert "{{classification_policy}}" not in system
    assert json.loads(payload)["inn"] == "тестовое вещество"


def test_policy_declares_every_supported_assessment():
    declared = set(
        re.findall(r"^- ([A-Z_]+)", load_classification_policy(), re.MULTILINE)
    )
    assert declared == {assessment.value for assessment in Assessment}


@pytest.mark.parametrize(
    "template", ["Инструкция без критериев", "{{classification_policy}}" * 2]
)
def test_invalid_policy_template_fails_explicitly(tmp_path, monkeypatch, template):
    directory = tmp_path / "instructions"
    directory.mkdir()
    (directory / "evidence_extraction.md").write_text(template, encoding="utf-8")
    monkeypatch.setattr(policy, "files", lambda _: tmp_path)
    with pytest.raises(ValueError, match="один маркер"):
        policy.load_system_prompt()


def test_missing_policy_has_no_silent_embedded_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "files", lambda _: tmp_path)
    with pytest.raises(FileNotFoundError):
        load_classification_policy()
