"""Рабочие схемы не зависят от специального режима входных данных."""

import pytest
from llm_rag.contracts import AnalysisRequest, AnalysisResult
from llm_rag.corpus import Corpus, Source
from llm_rag.explanation_contracts import ExplanationInput
from llm_rag.staff_contracts import StaffFacts


@pytest.mark.parametrize(
    "model", [AnalysisRequest, AnalysisResult, Corpus, Source, ExplanationInput, StaffFacts]
)
def test_no_demo_flags_in_runtime_contracts(model):
    assert "is_demo" not in model.model_fields
