"""Изолированный ML-2 блок: ГРЛС, КР, классификация и вход для LLM/RAG."""

from .classifier import (
    ClassificationResult,
    EvidenceFragment,
    classify_pair,
    gigachat_completer,
)
from .pipeline import ML2Batch, ML2Pipeline
from .registry import RegistrySnapshot

__all__ = [
    "ClassificationResult",
    "EvidenceFragment",
    "ML2Batch",
    "ML2Pipeline",
    "RegistrySnapshot",
    "classify_pair",
    "gigachat_completer",
]
