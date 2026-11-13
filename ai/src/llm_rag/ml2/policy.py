"""Общие читаемые инструкции ML-2 для классификатора и библиотеки RAG."""

import re
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_POLICY_MARKER = "{{classification_policy}}"
_ASSESSMENTS_MARKER = "{{assessment_rules}}"


class AssessmentRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Literal["А", "Б", "В"] | None
    description: str = Field(min_length=1)
    reason: str | None = None
    quote_patterns: tuple[str, ...] = ()
    same_clause_inn: bool = False
    reject_patterns: tuple[str, ...] = ()

    @field_validator("quote_patterns", "reject_patterns")
    @classmethod
    def valid_patterns(cls, patterns):
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError("Некорректный шаблон проверки цитаты") from error
        return patterns

    @model_validator(mode="after")
    def category_needs_reason_and_evidence(self):
        if self.category and (not self.reason or not self.quote_patterns):
            raise ValueError("Для категории нужны причина и шаблоны подтверждения роли")
        return self


class ClassificationRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1)
    mode: Literal["CLINICAL_RECOMMENDATIONS"]
    assessments: dict[str, AssessmentRule]


def _read_instruction(name: str) -> str:
    text = (
        files("llm_rag.ml2")
        .joinpath("instructions", name)
        .read_text(encoding="utf-8")
        .strip()
    )
    if not text:
        raise ValueError(f"Пустой файл инструкции ML-2: {name}")
    return text


def load_classification_policy() -> str:
    """Прочитать определения категорий и условия неопределённости для ML-2/RAG.

    Это инструкция интерпретации, а не Corpus согласованных правил для пар
    МНН + диагноз и не независимый снимок клинических источников.
    """
    template = _read_instruction("classification_policy.md")
    if template.count(_ASSESSMENTS_MARKER) != 1:
        raise ValueError("Критерии ML-2 должны содержать один маркер правил.")
    rules = load_classification_rules()
    definitions = "\n".join(
        f"- {name}"
        + (f" ({rule.category})" if rule.category else "")
        + f": {rule.description}"
        for name, rule in rules.assessments.items()
    )
    return (
        f"Версия правил: {rules.policy_version}. Режим: {rules.mode}.\n\n"
        + template.replace(_ASSESSMENTS_MARKER, definitions)
    )


def load_classification_rules() -> ClassificationRules:
    """Единый источник соответствия ролей категориям, текста и проверок цитат."""
    return ClassificationRules.model_validate_json(
        _read_instruction("classification_rules.json")
    )


def load_system_prompt() -> str:
    """Вставить общий файл критериев в инструкцию извлечения тезисов."""
    template = _read_instruction("evidence_extraction.md")
    if template.count(_POLICY_MARKER) != 1:
        raise ValueError("Инструкция ML-2 должна содержать один маркер критериев.")
    return template.replace(_POLICY_MARKER, load_classification_policy())
