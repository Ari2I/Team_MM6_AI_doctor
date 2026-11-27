"""Сборка ресурсов публичного фасада из официальных справочников."""

from functools import lru_cache
from hashlib import sha256
import os
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from dotenv import dotenv_values
import httpx

from llm_rag.corpus import Corpus, normalize
from llm_rag.embeddings import corpus_embedder
from llm_rag.evidence_snapshot import EvidenceDocument, EvidenceSnapshot
from llm_rag.integration import IntegrationResources
from llm_rag.integration_budget import source_timeout
from llm_rag.ml2.clinicalrecs import collect_fragments_for_inn
from llm_rag.ml2.evidence_selection import EvidenceContextLimit
from llm_rag.ml2.grls import download_latest, build_registry_snapshot
from llm_rag.ml2.official import ClinicalSources, SelectionError, select_edition
from llm_rag.ml2.provider import ML2GigaChatProvider
from llm_rag.ml2.registry import RegistryIndex, RegistrySnapshot
from llm_rag.network_policy import NetworkPolicy
from llm_rag.paths import source_root
from llm_rag.rag_data import default_data_dir, load_data


def _registry(cache, deadline):
    archive, as_of, digest = download_latest(cache, deadline=deadline)
    path = cache / f"registry-{digest}.json"
    if not path.exists():
        build_registry_snapshot(archive, as_of, path)
    return RegistrySnapshot.load(path)


def _clinical(request, registry, sources, deadline, links, *, diagnosis_aliases=()):
    diagnoses = {d.diagnosis_id: d for d in request.diagnoses}
    purchases = {p.item_id: p for p in request.purchases}
    index = RegistryIndex(registry)
    issues, documents = {}, {}
    public_names = set()
    aliases = {(a.icd10, a.edition, normalize(a.name)): a.name for a in diagnosis_aliases}

    @lru_cache(maxsize=None)
    def select(code, age_group, edition):
        candidates = list(sources.candidates(code, age_group)) if code else []
        return select_edition(sources, candidates, code=code, age=age_group, edition=edition).recommendation

    for link in links:
        if monotonic() >= deadline:
            break
        diagnosis, purchase = diagnoses[link.diagnosis_id], purchases[link.item_id]
        key = (link.item_id, link.diagnosis_id)
        group = diagnosis.conditions.recommendation_population
        try:
            rec = select(diagnosis.icd10, group, diagnosis.recommendation_edition)
            name = normalize(diagnosis.name)
            approved_name = rec.title if name == normalize(rec.title) else aliases.get(
                (diagnosis.icd10, rec.edition_id, name)
            )
            if approved_name is None:
                issues[key] = "DIAGNOSIS_MISMATCH"
                continue
            public_names.add(approved_name)
            inn = index.resolve(purchase.name, purchase.dosage or "").mnn
            if not inn or "+" in inn:
                continue
            required = {"icd10": diagnosis.icd10}
            if diagnosis.recommendation_edition:
                required["recommendation_edition"] = diagnosis.recommendation_edition
            if rec.population in {"Дети", "Взрослые"}:
                required["age_group"] = rec.population
            for fragment in collect_fragments_for_inn(
                rec, inn, checkpoint=lambda: source_timeout(deadline, 1.0),
            ):
                # Один фрагмент может относиться к нескольким МНН и диагнозам заявки.
                identity = (
                    fragment.fragment_id,
                    diagnosis.name,
                    diagnosis.icd10,
                    diagnosis.recommendation_edition,
                    inn,
                )
                digest = sha256(repr(identity).encode()).hexdigest()[:24]
                documents[digest] = EvidenceDocument(
                    **(vars(fragment) | {"fragment_id": digest}),
                    diagnosis=diagnosis.name,
                    inn=inn,
                    required_context=required,
                    retrieved_at=rec.retrieved_at,
                )
        except SelectionError as error:
            issues[key] = error.status
        except EvidenceContextLimit:
            issues[key] = "CONTEXT_LIMIT"
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
            issues[key] = "SOURCE_ERROR"

    docs = [documents[k] for k in sorted(documents)]
    digest = sha256("".join(d.model_dump_json(exclude={"retrieved_at"}) for d in docs).encode()).hexdigest()
    return EvidenceSnapshot(snapshot_version="clinical-" + digest[:16], documents=docs), issues, public_names


def prepare_resources(request, verify_ssl_certs, options, start):
    links = request.links[:options.max_pairs]
    deadline = start + options.deadline_seconds
    if not links or monotonic() >= deadline:
        return IntegrationResources(verify_ssl_certs=verify_ssl_certs)
    root = source_root()
    settings = {
        **(dotenv_values(root / ".env", encoding="utf-8-sig", interpolate=False) if root else {}),
        **os.environ,
    }
    data = default_data_dir()
    try:
        registry = _registry(data / "cache/grls", deadline)
    except (ValueError, OSError, httpx.HTTPError):
        return IntegrationResources(verify_ssl_certs=verify_ssl_certs)
    # Расширять список публичных синонимов можно только настройкой развёртывания.
    terms = settings.get("RAG_NETWORK_TERMS")
    policy = NetworkPolicy.load(terms) if terms else NetworkPolicy()
    snapshot, issues, titles = _clinical(
        request, registry, ClinicalSources(deadline), deadline, links,
        diagnosis_aliases=policy.diagnosis_aliases,
    )
    policy = NetworkPolicy(
        tuple(sorted(set(policy.diagnoses) | titles)),
        tuple(sorted(set(policy.inns) | {r.mnn for r in registry.registrations if r.mnn})),
        policy.max_payload_bytes,
    )
    allowed = {" ".join(term.casefold().split()) for term in policy.diagnoses}
    diagnoses = {d.diagnosis_id: d for d in request.diagnoses}
    for link in links:
        diagnosis = diagnoses[link.diagnosis_id]
        if " ".join(diagnosis.name.casefold().split()) not in allowed:
            issues.setdefault((link.item_id, link.diagnosis_id), "NETWORK_POLICY_DENIED")
    approved = None
    if (data / "current.json").exists():
        installed = load_data(data)
        corpora = [r for r in installed.manifest.resources if r.kind == "corpus"]
        if len(corpora) > 1:
            raise ValueError("В поставке должен быть один утверждённый корпус.")
        if corpora:
            approved = Corpus.model_validate_json(
                installed.resource_path(corpora[0].resource_id).read_bytes()
            )
    ca = settings.get("GIGACHAT_CA_BUNDLE_FILE") or settings.get("GIGACHAT_CA_BUNDLE")
    if ca and root and not Path(ca).is_absolute():
        ca = str(root / ca)
    settings["GIGACHAT_CA_BUNDLE_FILE"] = ca

    def provider():
        return ML2GigaChatProvider(
            credentials=settings.get("GIGACHAT_CREDENTIALS"),
            access_token=settings.get("GIGACHAT_ACCESS_TOKEN"),
            enabled=True,
            model=settings.get("GIGACHAT_MODEL") or "GigaChat-2",
            scope=settings.get("GIGACHAT_SCOPE") or "GIGACHAT_API_B2B",
            verify_ssl_certs=verify_ssl_certs,
            ca_bundle=ca,
            timeout=source_timeout(deadline, options.deadline_seconds),
            network_policy=policy,
        )

    def embeddings(*, before_call):
        corpus = SimpleNamespace(
            fragments=[d for d in snapshot.documents if " ".join(d.diagnosis.casefold().split()) in allowed]
        )
        return corpus_embedder(
            corpus,
            settings,
            allow_network=True,
            before_call=before_call,
            cache_path=data / "indexes/official-embeddings.json",
            verify_ssl_certs=verify_ssl_certs,
            timeout=source_timeout(deadline, options.deadline_seconds),
        )

    return IntegrationResources(
        registry=registry,
        evidence=snapshot,
        source_issues=issues,
        approved_corpus=approved,
        provider_factory=provider,
        embedding_factory=embeddings,
        verify_ssl_certs=verify_ssl_certs,
    )
