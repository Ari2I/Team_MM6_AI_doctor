"""Вход integration-request.1: явные пары и неполные исходные сведения.

Модели не открывают сеть и не запускают обработку. Денежные значения и
количество в JSON передаются десятичными строками для сохранения точности.
Условия остаются локальными: сериализация не разрешает их отправку провайдеру.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from llm_rag.contracts import Category, Model, Money, PurchaseMoney, Text, RetrievalInfo
from llm_rag.explanation_contracts import EvidenceCitation, EvidenceStatus, PairKey, PairExplanation, pair_key
from llm_rag.evidence_snapshot import EvidenceSnapshot
from llm_rag.corpus import normalize
from llm_rag.staff_contracts import StaffAnalysis


AgeGroup = Literal["adult", "child"]


class LocalConditions(Model):
    """Известные условия применимости; отсутствие значения не означает отрицание."""

    age_years: Money | None = None
    age_group: AgeGroup | None = None
    pregnant: bool | None = None

    def _group_from_years(self) -> AgeGroup | None:
        if self.age_years is None:
            return None
        return "adult" if self.age_years >= 18 else "child"

    @model_validator(mode="after")
    def validate_age_group(self) -> Self:
        group_from_years = self._group_from_years()
        if self.age_group is not None and group_from_years is not None and self.age_group != group_from_years:
            raise ValueError("Возрастная группа age_group противоречит точному возрасту age_years.")
        return self

    @property
    def recommendation_population(self) -> str | None:
        """Возрастная группа в обозначениях рубрикатора Минздрава."""
        group = self._group_from_years() or self.age_group
        return {"adult": "Взрослые", "child": "Дети"}.get(group)


class IntegrationDiagnosis(Model):
    recommendation_edition: Text | None = None
    diagnosis_id: Text
    name: Text
    icd10: Annotated[str, Field(pattern=r"^[A-Z][0-9]{2}(?:\.[0-9]{1,4})?$")] | None = None
    conditions: LocalConditions = Field(default_factory=LocalConditions)


class IntegrationPurchase(Model):
    item_id: Text
    name: Text
    inn: Text | None = None
    dosage: Text | None = None
    quantity: Annotated[Money, Field(gt=0)] | None = None
    amount: PurchaseMoney | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None


class IntegrationLink(Model):
    item_id: Text
    diagnosis_id: Text


class PinnedVersions(Model):
    """Независимые версии ресурсов; None не подменяется текущей версией."""

    registry: Text | None = None
    clinical: Text | None = None
    corpus: Text | None = None
    policy: Text | None = None


class IntegrationRequest(Model):
    schema_version: Literal["integration-request.1"]
    request_id: Text
    diagnoses: Annotated[list[IntegrationDiagnosis], Field(min_length=1)]
    purchases: Annotated[list[IntegrationPurchase], Field(min_length=1)]
    links: Annotated[list[IntegrationLink], Field(min_length=1)]
    versions: PinnedVersions = Field(default_factory=PinnedVersions)

    @model_validator(mode="after")
    def validate_identity(self):
        diagnoses = [d.diagnosis_id for d in self.diagnoses]
        items = [p.item_id for p in self.purchases]
        if len(set(diagnoses)) != len(diagnoses):
            raise ValueError("ID диагноза должен быть уникальным в заявке.")
        if len(set(items)) != len(items):
            raise ValueError("ID покупки должен быть уникальным в заявке.")
        diagnosis_ids, item_ids = set(diagnoses), set(items)
        pairs = set()
        for link in self.links:
            if link.item_id not in item_ids or link.diagnosis_id not in diagnosis_ids:
                raise ValueError("Связь ссылается на отсутствующую покупку или диагноз.")
            pair = (link.item_id, link.diagnosis_id)
            if pair in pairs:
                raise ValueError("Пара покупки и диагноза указана повторно.")
            pairs.add(pair)
        return self


ReviewReason = Literal[
    "MODEL_DISABLED",
    "NEEDS_REVIEW",
    "DIAGNOSIS_MISMATCH",
    "NO_EVIDENCE",
    "INN_UNRESOLVED",
    "SOURCE_ERROR",
    "AI_TIMEOUT",
    "AI_INVALID_RESPONSE",
    "AI_UNAVAILABLE",
    "BUDGET_EXHAUSTED",
    "VERSION_MISMATCH",
    "CATEGORY_CONFLICT",
    "NEEDS_CONTEXT",
    "FORM_MISMATCH",
    "INTERNAL_ERROR",
    "NETWORK_POLICY_DENIED",
    "CONTEXT_LIMIT",
]


class CategoryDecision(Model):
    upstream_rule_category: Category | None = None
    proposed_category: Category | None = None
    classification_basis: Literal["APPROVED_RULE", "ML2_PROPOSAL", "CONFLICT", "NONE"]
    requires_review: Literal[True] = True

    @model_validator(mode="after")
    def consistent_basis(self):
        upstream, proposal = self.upstream_rule_category, self.proposed_category
        expected = (
            "CONFLICT"
            if upstream and proposal and upstream != proposal
            else "APPROVED_RULE"
            if upstream
            else "ML2_PROPOSAL"
            if proposal
            else "NONE"
        )
        if self.classification_basis != expected:
            raise ValueError("Основание не соответствует исходным категориям.")
        return self


class IntegrationPair(PairKey):
    decision: CategoryDecision
    inn: Text | None = None
    policy_version: Text | None = None
    evidence_status: EvidenceStatus
    reasons: list[ReviewReason] = Field(default_factory=list)
    conditions: list[Text] = Field(default_factory=list)
    citations: list[EvidenceCitation] = Field(default_factory=list)
    explanation: PairExplanation | None = None
    form_status: Literal["MATCH", "MISMATCH", "UNKNOWN"] = "UNKNOWN"
    form_reason: Text = "Форма не сопоставлена."
    generation_mode: Literal["OFFLINE", "MODEL", "FALLBACK"] = "OFFLINE"
    staff_actions: list[Text] = Field(default_factory=list)
    staff_analysis: StaffAnalysis | None = None


class UnprocessedPair(PairKey):
    reason: ReviewReason


class ResourceVersion(Model):
    version: Text
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class IntegrationManifest(Model):
    registry: ResourceVersion | None = None
    clinical: ResourceVersion | None = None
    corpus: ResourceVersion | None = None
    policy: ResourceVersion | None = None


class IntegrationUsage(Model):
    calls: Annotated[int, Field(ge=0)] = 0
    cache_hits: Annotated[int, Field(ge=0)] = 0
    tokens: Annotated[int, Field(ge=0)] = 0
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    token_accounting: Literal["ACTUAL", "UPPER_BOUND"] = "ACTUAL"


class PurchaseTotals(Model):
    totals: dict[str, Money] = Field(default_factory=dict)
    unknown_amount_item_ids: list[Text] = Field(default_factory=list)
    unknown_currency_item_ids: list[Text] = Field(default_factory=list)


class IntegrationResult(Model):
    schema_version: Literal["integration-result.1"] = "integration-result.1"
    request: IntegrationRequest
    pairs: list[IntegrationPair] = Field(default_factory=list)
    unprocessed_pairs: list[UnprocessedPair] = Field(default_factory=list)
    completeness: Literal["COMPLETE", "PARTIAL"] = "PARTIAL"
    manifest: IntegrationManifest = Field(default_factory=IntegrationManifest)
    evidence_snapshot: EvidenceSnapshot | None = None
    costs: PurchaseTotals | None = None
    usage: IntegrationUsage = Field(default_factory=IntegrationUsage)
    human_report: dict = Field(default_factory=dict)
    retrieval_info: RetrievalInfo | None = None
    lifecycle_status: Literal["NOT_CREATED", "CLOSED", "CLOSE_FAILED"] = "NOT_CREATED"

    @model_validator(mode="after")
    def verify_result(self):
        from hashlib import sha256
        from llm_rag.integration_policy import purchase_totals

        expected_costs = purchase_totals(self.request)
        if self.costs is None:
            object.__setattr__(self, "costs", expected_costs)
        elif self.costs != expected_costs:
            raise ValueError("Денежные итоги не соответствуют исходным покупкам.")
        expected = {(self.request.request_id, p.item_id, p.diagnosis_id) for p in self.request.links}
        actual = [(p.request_id, p.item_id, p.diagnosis_id) for p in self.pairs + self.unprocessed_pairs]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError("Результат должен содержать каждую запрошенную пару ровно один раз.")
        if self.completeness == "COMPLETE" and self.unprocessed_pairs:
            raise ValueError("Полный результат не может содержать необработанные пары.")
        if self.pairs:
            for key in ("registry", "clinical", "corpus", "policy"):
                pinned = getattr(self.request.versions, key)
                actual = getattr(self.manifest, key)
                if pinned is not None and (actual is None or actual.version != pinned):
                    raise ValueError(
                        "Обработанная пара использует ресурс, не совпадающий с закреплённой версией."
                    )
        if self.evidence_snapshot is not None:
            resource = self.manifest.clinical
            if (
                resource is None
                or resource.version != self.evidence_snapshot.snapshot_version
                or resource.sha256 != sha256(self.evidence_snapshot.model_dump_json().encode()).hexdigest()
            ):
                raise ValueError("Версия или SHA клинического снимка не совпадает.")
        docs = (
            {(d.source_id, d.fragment_id, d.version): d for d in self.evidence_snapshot.documents}
            if self.evidence_snapshot
            else {}
        )
        diagnoses = {d.diagnosis_id: d for d in self.request.diagnoses}
        for pair in self.pairs:
            if pair.policy_version is not None and (
                self.manifest.policy is None or pair.policy_version != self.manifest.policy.version
            ):
                raise ValueError("Версия политики пары не совпадает с manifest.")
            if (
                pair.decision.upstream_rule_category or pair.decision.proposed_category
            ) and pair.policy_version is None:
                raise ValueError("Для категории нужна версия использованной политики.")
            if pair.staff_analysis is not None:
                expected_category = pair.decision.upstream_rule_category or pair.decision.proposed_category
                if pair.decision.classification_basis == "CONFLICT":
                    expected_category = None
                if (
                    pair.staff_analysis.pair_id != pair_key(pair)
                    or pair.staff_analysis.category != expected_category
                    or pair.staff_analysis.citations != pair.citations
                ):
                    raise ValueError("Разбор сотруднику изменил категорию или цитаты пары.")
                known_facts = {
                    "diagnosis",
                    "inn",
                    "category",
                    "ml2_status",
                    "evidence_status",
                    "classification_basis",
                    "ml2_status_meaning",
                }
                for statement in (
                    pair.staff_analysis.established
                    + pair.staff_analysis.uncertain
                    + pair.staff_analysis.questions
                ):
                    if not set(statement.fact_refs) <= known_facts or any(
                        not ref.isdigit() or int(ref) >= len(pair.citations)
                        for ref in statement.citation_refs
                    ):
                        raise ValueError("Разбор сотруднику содержит неизвестные ссылки на факты или цитаты.")
            if pair.explanation is not None and (
                pair.explanation.request_id,
                pair.explanation.item_id,
                pair.explanation.diagnosis_id,
            ) != (pair.request_id, pair.item_id, pair.diagnosis_id):
                raise ValueError("Пояснение относится к другой паре.")
            if pair.explanation is not None and pair.explanation.citations != pair.citations:
                raise ValueError("Цитаты пояснения отличаются от проверенных цитат пары.")
            if pair.explanation is not None:
                explanation = pair.explanation
                category = pair.decision.upstream_rule_category or pair.decision.proposed_category
                if pair.decision.classification_basis == "CONFLICT":
                    category = None
                corpus_version = self.manifest.corpus.version if self.manifest.corpus else None
                if (
                    explanation.category != category
                    or explanation.evidence_status != pair.evidence_status
                    or not self.manifest.clinical
                    or explanation.snapshot_version != self.manifest.clinical.version
                    or explanation.approved_corpus_version != corpus_version
                ):
                    raise ValueError(
                        "Категория, статус или версии пояснения не соответствуют паре и manifest."
                    )
            if pair.evidence_status == "VERIFIED" and not pair.citations:
                raise ValueError("Проверенное основание требует точных цитат.")
            for citation in pair.citations:
                doc = docs.get((citation.source_id, citation.fragment_id, citation.version))
                if (
                    doc is None
                    or citation.quote not in doc.text
                    or citation.url != doc.url
                    or citation.copy_url != doc.copy_url
                    or citation.title != doc.title
                    or citation.location != doc.location
                    or normalize(doc.diagnosis) != normalize(diagnoses[pair.diagnosis_id].name)
                    or normalize(doc.inn) != normalize(pair.inn or "")
                ):
                    raise ValueError("Цитата не подтверждена снимком этой пары.")
        from llm_rag.integration_report import build_integration_human_report

        expected_report = build_integration_human_report(
            self.request, self.pairs, self.unprocessed_pairs, self.costs
        )
        if not self.human_report:
            object.__setattr__(self, "human_report", expected_report)
        elif self.human_report != expected_report:
            raise ValueError("Публичный отчёт не соответствует проверенным результатам.")
        return self
