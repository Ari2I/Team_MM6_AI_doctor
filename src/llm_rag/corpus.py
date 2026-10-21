"""Загрузка версионированного синтетического корпуса с ручной разметкой без скачивания документов."""

from typing import Literal

from pydantic import Field, model_validator

from llm_rag.contracts import Category, DemoFlag, Model, Text


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


class Source(Model):
    source_id: Text
    title: Text
    source_type: Literal["MASTER_LIST", "PROTOCOL", "DOSAGE"]
    version: Text
    is_demo: DemoFlag


class Assertion(Model):
    claim_id: Text
    kind: Literal["CATEGORY", "DOSAGE"]
    category: Category | None
    dosage_status: Literal["VALID", "INVALID"] | None
    dosage: Text | None
    upstream_reason: Text
    text: Text
    quote: Text

    @model_validator(mode="after")
    def coherent_kind(self):
        if self.kind == "CATEGORY":
            if self.category is None or self.dosage_status is not None or self.dosage is not None:
                raise ValueError("CATEGORY assertion must carry only a category")
        elif self.category is not None or self.dosage_status is None or self.dosage is None:
            raise ValueError("DOSAGE assertion must carry a result and exact upstream dosage")
        # Допускается только точное извлечение текста, без произвольного перефразирования.
        if self.text not in self.quote:
            raise ValueError("Approved text must be a literal extract of its quote")
        return self


class Fragment(Model):
    fragment_id: Text
    source_id: Text
    location: Text
    inn: Text
    diagnosis: Text
    required_context: dict[Text, Text]
    text: Text
    assertions: list[Assertion]

    @model_validator(mode="after")
    def quotes_exist(self):
        if any(a.quote not in self.text for a in self.assertions):
            raise ValueError("Every assertion quote must literally occur in its fragment")
        return self


class Corpus(Model):
    corpus_version: Text
    is_demo: DemoFlag
    sources: list[Source] = Field(min_length=1)
    fragments: list[Fragment] = Field(min_length=1)

    @model_validator(mode="after")
    def integrity(self):
        for values, label in [
            ([s.source_id for s in self.sources], "source_id"),
            ([f.fragment_id for f in self.fragments], "fragment_id"),
            ([a.claim_id for f in self.fragments for a in f.assertions], "claim_id"),
        ]:
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {label}")
        source_ids = {s.source_id for s in self.sources}
        if any(f.source_id not in source_ids for f in self.fragments):
            raise ValueError("Fragment references an unknown source")
        return self
