"""Версия 2: факты и свободный разбор сотруднику с проверяемыми ссылками."""

from typing import Annotated, Literal
from pydantic import Field
from llm_rag.contracts import Model, Category
from llm_rag.explanation_contracts import EvidenceCitation

BoundedText = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]
Refs = Annotated[list[BoundedText], Field(min_length=1, max_length=20)]


class StaffObservation(Model):
    text: BoundedText
    fact_refs: Refs
    citation_refs: list[BoundedText]


class StaffQuestion(StaffObservation):
    why: BoundedText


class StaffAnalysis(Model):
    pair_id: BoundedText
    category: Category | None
    established: Annotated[list[StaffObservation], Field(min_length=1, max_length=8)]
    uncertain: Annotated[list[StaffObservation], Field(min_length=1, max_length=8)]
    questions: Annotated[list[StaffQuestion], Field(min_length=1, max_length=8)]
    citations: list[EvidenceCitation]


class StaffAnalysisResponse(Model):
    schema_version: Literal['staff-analysis.2']
    recommendations: Annotated[list[StaffAnalysis], Field(min_length=1, max_length=2)]


class StaffFactPair(Model):
    pair_id: str
    facts: dict
    citations: list[EvidenceCitation]


class StaffFacts(Model):
    schema_version: Literal['staff-facts.2'] = 'staff-facts.2'
    is_demo: Literal[True] = True
    pairs: Annotated[list[StaffFactPair], Field(min_length=1, max_length=1)]


class StaffWireAnalysis(Model):
    """Компактный транспорт: свободный текст, неизменяемые факты остаются в коде."""

    pair_id: BoundedText
    uncertain: BoundedText
    question: BoundedText
    why: BoundedText
    fact_refs: Refs
    citation_refs: list[BoundedText]


def staff_wire_schema(packet):
    """Не разворачивать большие пределы строк в серверном декодере; проверять их локально."""
    text = dict(type='string', minLength=1)
    fields = dict(pair_id=dict(type='string', enum=[packet.pair_id]),
        uncertain=dict(text), question=dict(text), why=dict(text),
        fact_refs=dict(type='array', minItems=1, items=dict(type='string', enum=list(packet.facts))),
        citation_refs=dict(type='array', items=dict(type='string')))
    if packet.citations:
        fields['citation_refs']['items']['enum'] = [str(i) for i in range(len(packet.citations))]
    else:
        fields['citation_refs']['maxItems'] = 0
    return dict(type='object', additionalProperties=False, properties=fields, required=list(fields))
