"""Изолированный настоящий worker с синтетическими внешними границами теста.

Запускается только тестами. Не читает файлы заявок/ключи и запрещает сеть.
"""

import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path


def synthetic_resources(request, verify_ssl_certs, options, start):
    from llm_rag.evidence_snapshot import EvidenceDocument, EvidenceSnapshot
    from llm_rag.integration import IntegrationResources
    from llm_rag.ml2.registry import RegistryEntry, RegistrySnapshot, RegistrySource
    from llm_rag.network_policy import NetworkPolicy

    assert verify_ssl_certs is True
    diagnoses = {d.diagnosis_id: d for d in request.diagnoses}
    inn = "Синтетическое вещество"
    source = RegistrySource(
        title="Синтетический реестр",
        url="https://grls.rosminzdrav.ru/GRLS.aspx",
        version="synthetic-registry-1",
        location="1",
        retrieved_at=date(2026, 11, 27),
        quote="Синтетическая запись, не медицинское основание.",
    )
    registry = RegistrySnapshot(
        version="synthetic-registry-1",
        as_of=date(2026, 11, 27),
        registrations=tuple(
            RegistryEntry(
                registration=str(n),
                trade_name=p.name,
                mnn=inn,
                source=source,
            )
            for n, p in enumerate(request.purchases)
        ),
    )
    documents = []
    for n, link in enumerate(request.links):
        diagnosis = diagnoses[link.diagnosis_id]
        context = {}
        if diagnosis.recommendation_edition:
            context["recommendation_edition"] = diagnosis.recommendation_edition
        if diagnosis.icd10:
            context["icd10"] = diagnosis.icd10
        if diagnosis.conditions.recommendation_population:
            context["age_group"] = diagnosis.conditions.recommendation_population
        documents.append(
            EvidenceDocument(
                source_id=f"synthetic-source-{n}",
                fragment_id=f"synthetic-fragment-{n}",
                version=diagnosis.recommendation_edition or "synthetic-edition-1",
                title="Синтетическое основание, не клиническое заключение",
                url="https://example.org/synthetic-recommendation",
                location=f"Синтетический абзац {n}",
                text="Синтетическое вещество рекомендуется для лечения заболевания.",
                diagnosis=diagnosis.name,
                inn=inn,
                required_context=context,
                retrieved_at="2026-11-27T00:00:00Z",
            )
        )

    class Client:
        scope = "GIGACHAT_API_B2B"
        verify_ssl_certs = True
        network_policy = NetworkPolicy(
            diagnoses=tuple(d.name for d in request.diagnoses), inns=(inn,)
        )

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        @contextmanager
        def request_limits(self, **kwargs):
            yield

        def complete_json(self, payload, system, schema):
            fragment = payload["fragments"][0]
            return {
                "assessment": "CORE_TREATMENT",
                "conditions": [],
                "missing_context": [],
                "evidence": [
                    {"fragment_id": fragment["fragment_id"], "quote": fragment["quote_options"][0]}
                ],
            }

        def generate(self, payload):
            recommendations = []
            for pair in payload["pairs"]:
                recommendations.append(
                    dict(
                        pair_id=pair["pair_id"],
                        category=pair["facts"]["category"],
                        established=[
                            dict(
                                text="Получен результат классификации.",
                                fact_refs=["ml2_status"],
                                citation_refs=[],
                            )
                        ],
                        uncertain=[
                            dict(
                                text="Применимость требует сверки.",
                                fact_refs=["evidence_status"],
                                citation_refs=[],
                            )
                        ],
                        questions=[
                            dict(
                                text="Сверены ли условия основания?",
                                why="Нужна проверка специалиста.",
                                fact_refs=["diagnosis"],
                                citation_refs=[],
                            )
                        ],
                        citations=pair["citations"],
                    )
                )
            return dict(schema_version="staff-analysis.2", recommendations=recommendations)

    return IntegrationResources(
        registry=registry,
        evidence=EvidenceSnapshot(snapshot_version="synthetic-snapshot-1", documents=documents),
        provider_factory=Client,
    )


def main():
    attempts = []

    def guard(event, args):
        if event in {"socket.__new__", "socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            attempts.append(event)
            raise PermissionError("NETWORK_FORBIDDEN")

    sys.addaudithook(guard)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai/src"))
    from llm_rag import runtime

    from app.services.integration_worker import main as worker_main

    runtime.prepare_resources = synthetic_resources
    worker_main()
    if attempts:
        raise RuntimeError("TEST_WORKER_ATTEMPTED_NETWORK")


if __name__ == "__main__":
    main()
