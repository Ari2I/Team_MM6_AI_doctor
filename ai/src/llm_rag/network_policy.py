"""Единый разрешённый сетевой набор. Списки терминов задаются из проверенных ресурсов."""

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Annotated

from pydantic import Field, TypeAdapter

from llm_rag.contracts import Model, Text
from llm_rag.providers.base import ProviderError


class DiagnosisAlias(Model):
    icd10: Annotated[str, Field(pattern=r"^[A-Z][0-9]{2}(?:\.[0-9]{1,4})?$")]
    edition: Annotated[str, Field(pattern=r"^[0-9]+_[0-9]+$")]
    name: Text


@dataclass(frozen=True)
class NetworkPolicy:
    diagnoses: tuple[str, ...] = ()
    inns: tuple[str, ...] = ()
    max_payload_bytes: int = 100000
    diagnosis_aliases: tuple[DiagnosisAlias, ...] = ()

    def __post_init__(self):
        if type(self.max_payload_bytes) is not int or self.max_payload_bytes <= 0:
            raise ValueError("Предел сетевого пакета должен быть положительным целым.")
        for name in ("diagnoses", "inns"):
            terms = tuple(getattr(self, name))
            if any(not isinstance(term, str) or not term.strip() for term in terms):
                raise ValueError("Разрешающий список должен содержать непустые публичные термины.")
            object.__setattr__(self, name, terms)

    @classmethod
    def from_corpus(cls, corpus):
        """Корпус должен быть предварительно допущен владельцем данных к внешней передаче."""
        return cls(
            tuple(dict.fromkeys(f.diagnosis for f in corpus.fragments)),
            tuple(dict.fromkeys(f.inn for f in corpus.fragments)),
        )

    @classmethod
    def load(cls, path):
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            not {"diagnoses", "inns"} <= set(value)
            or set(value) - {"diagnoses", "inns", "max_payload_bytes", "diagnosis_aliases"}
            or any(
                not isinstance(value[k], list)
                or any(not isinstance(term, str) or not term.strip() for term in value[k])
                for k in ("diagnoses", "inns")
            )
        ):
            raise ValueError("Нужны списки допущенных публичных медицинских терминов diagnoses/inns.")
        aliases = TypeAdapter(tuple[DiagnosisAlias, ...]).validate_python(value.get("diagnosis_aliases", ()))
        return cls(tuple(value["diagnoses"]), tuple(value["inns"]), value.get("max_payload_bytes", 100000), aliases)


def _term(value, allowed):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderError("NETWORK_POLICY_DENIED")
    key = " ".join(value.casefold().split())
    for term in allowed:
        if " ".join(term.casefold().split()) == key:
            return term
    raise ProviderError("NETWORK_POLICY_DENIED")


def _source_fields(value, allowed, *, required=(), nullable=()):
    if not isinstance(value, dict) or any(key not in value for key in required):
        raise ProviderError("INVALID_PAYLOAD")
    result = {}
    for key in allowed:
        if key not in value:
            continue
        item = value[key]
        if not isinstance(item, str) and not (item is None and key in nullable):
            raise ProviderError("NETWORK_POLICY_DENIED")
        result[key] = item
    return result


def _check_size(value, policy):
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > policy.max_payload_bytes:
        raise ProviderError("NETWORK_CONTEXT_TOO_LARGE")
    return value


def prepare_classification(payload, policy):
    """Возраст и произвольные условия не пересекают внешнюю границу."""
    policy = policy or NetworkPolicy()
    fragments = []
    for fragment in payload.get("fragments", []):
        fields = ("fragment_id", "source_id", "title", "version", "location", "text")
        selected = _source_fields(fragment, fields, required=fields)
        if "quote_options" in fragment:
            if not isinstance(fragment["quote_options"], list) or any(
                not isinstance(q, str) for q in fragment["quote_options"]
            ):
                raise ProviderError("NETWORK_POLICY_DENIED")
            selected["quote_options"] = list(fragment["quote_options"])
        fragments.append(selected)
    return _check_size(
        dict(
            diagnosis=_term(payload.get("diagnosis"), policy.diagnoses),
            inn=_term(payload.get("inn"), policy.inns),
            fragments=fragments,
        ),
        policy,
    )


def prepare_payload(payload, policy):
    """Собрать минимальный пакет и обратное отображение временного ключа."""
    policy = policy or NetworkPolicy()
    schema = payload.get("schema_version")
    original = None
    if schema in {"1.0", "explanation.1"}:
        item = payload.get("item", {})
        original = item.get("item_id")
        if not isinstance(original, str) or not original.strip():
            raise ProviderError("INVALID_PAYLOAD")
        safe = dict(
            schema_version=schema,
            corpus_version=payload.get("corpus_version"),
            item=dict(item_id="position-1"),
            evidence=[],
        )
        for field, terms in [("diagnosis", policy.diagnoses), ("inn", policy.inns)]:
            if field in item:
                safe["item"][field] = _term(item[field], terms)
        if "category" in item:
            if item["category"] not in {"А", "Б", "В", None}:
                raise ProviderError("NETWORK_POLICY_DENIED")
            safe["item"]["category"] = item["category"]
        for evidence in payload.get("evidence", []):
            safe["evidence"].append(
                _source_fields(
                    evidence,
                    (
                        "claim_id",
                        "kind",
                        "text",
                        "quote",
                        "fragment_id",
                        "source_id",
                        "source_title",
                        "source_version",
                        "location",
                        "fragment_text",
                    ),
                    required=("claim_id", "fragment_id", "text", "quote"),
                )
            )
    elif schema == "staff-facts.2":
        packets = payload.get("pairs", [])
        if len(packets) != 1:
            raise ProviderError("NETWORK_POLICY_DENIED")
        packet = packets[0]
        original = packet["pair_id"]
        facts = packet["facts"]
        from typing import get_args
        from llm_rag.explanation_contracts import ML2Status, EvidenceStatus

        enums = {
            "category": {"А", "Б", "В", None},
            "ml2_status": set(get_args(ML2Status)),
            "evidence_status": set(get_args(EvidenceStatus)),
            "classification_basis": {"APPROVED_RULE", "ML2_PROPOSAL", "NONE"},
        }
        safe_facts = {}
        for key, allowed in enums.items():
            if facts.get(key) not in allowed:
                raise ProviderError("NETWORK_POLICY_DENIED")
            safe_facts[key] = facts[key]
        for key, terms in [("diagnosis", policy.diagnoses), ("inn", policy.inns)]:
            safe_facts[key] = _term(facts.get(key), terms)
        from llm_rag.explanation import STATUS_REASONS

        safe_facts["ml2_status_meaning"] = STATUS_REASONS.get(
            facts.get("ml2_status"), "Результат автоматической проверки."
        )
        citations = [
            _source_fields(
                citation,
                ("source_id", "fragment_id", "title", "version", "url", "copy_url", "location", "quote"),
                nullable=("copy_url",),
                required=("source_id", "fragment_id", "version", "url", "location", "quote"),
            )
            for citation in packet["citations"]
        ]
        safe = dict(
            schema_version=schema, pairs=[dict(pair_id="position-1", facts=safe_facts, citations=citations)]
        )
    else:
        raise ProviderError("INVALID_PAYLOAD")
    if schema in {"1.0", "explanation.1"}:
        from llm_rag.generation import INSTRUCTIONS

        safe["instructions"] = INSTRUCTIONS
    _check_size(safe, policy)

    def restore(value):
        result = deepcopy(value)

        def visit(obj):
            if isinstance(obj, dict):
                for key, child in obj.items():
                    if key in {"pair_id", "item_id"} and child == "position-1":
                        obj[key] = original
                    else:
                        visit(child)
            elif isinstance(obj, list):
                for child in obj:
                    visit(child)

        visit(result)
        return result

    return safe, restore
