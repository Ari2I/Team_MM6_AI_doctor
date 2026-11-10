"""Точка интеграции библиотеки с независимой и осторожной обработкой ошибок каждой позиции."""

from uuid import uuid4

from llm_rag.contracts import AnalysisItem, AnalysisRequest, AnalysisResult, ItemResult
from llm_rag.corpus import Corpus
from llm_rag.evidence import build_evidence
from llm_rag.embeddings import Embedder
from llm_rag.generation import build_payload
from llm_rag.summary import build_summary, calculate_costs
from llm_rag.providers import Provider
from llm_rag.providers.base import ProviderError, PROVIDER_ERROR_MESSAGES
from llm_rag.retrieval import Retriever
from llm_rag.validation import GenerationError, citation_for, validate_generation


STATUS_TEXT = {
    "SUPPORTED": "Обоснование подтверждено тестовыми источниками.",
    "INSUFFICIENT_DATA": "Недостаточно данных; необходимо дополнить сведения.",
    "UNCONFIRMED": "Обоснование не подтверждено: недостаточно данных в корпусе или проверенном ответе.",
    "SOURCE_CONFLICT": "Обнаружено противоречие источнику; требуется экспертная проверка. Категория №2 сохранена.",
    "PROCESSING_ERROR": "Техническая ошибка обработки; медицинское обоснование не сформировано.",
}


class Pipeline:
    def __init__(self, corpus: Corpus, provider: Provider, *, embedder: Embedder | None = None):
        self._corpus = Corpus.model_validate(corpus.model_dump())
        self._retriever = Retriever(self._corpus, embedder=embedder)
        self._provider = provider

    def run(self, request: AnalysisRequest) -> AnalysisResult:
        # Повторно проверяем данные и создаём копию на случай изменения вложенных списков модели.
        request = AnalysisRequest.model_validate(request.model_dump())
        results = []
        for item in request.items:
            try:
                result = self._process_item(request, item)
            except Exception as error:
                # Не раскрываем исходные ошибки SDK/провайдера, учётные данные и сведения о пациентах.
                reasons = ["ITEM_PROCESSING_ERROR"]
                if isinstance(error, (ProviderError, GenerationError)) and len(error.args) == 1:
                    code = error.args[0]
                    if isinstance(code, str) and code in PROVIDER_ERROR_MESSAGES:
                        reasons.append(code)
                result = ItemResult(
                    **item.model_dump(), explanation_status="PROCESSING_ERROR",
                    comment=STATUS_TEXT["PROCESSING_ERROR"], claims=[], review_sources=[],
                    reasons=reasons, explanation_missing_data=[],
                )
            results.append(result)
        text, refs, completeness, unresolved = build_summary(results)
        return AnalysisResult(
            request_id=request.request_id, diagnosis=request.diagnosis, items=results,
            summary_text=text, summary_refs=refs, requires_review=bool(unresolved),
            unresolved_item_ids=unresolved, completeness=completeness,
            costs_by_currency=calculate_costs(request), corpus_version=self._corpus.corpus_version,
            run_id=str(uuid4()), provider_name=self._provider.name,
            retrieval_info=self._retriever.info.model_copy(deep=True),
        )

    def _process_item(self, request: AnalysisRequest, item: AnalysisItem) -> ItemResult:
        fragments = self._retriever.search(item, request.diagnosis)
        packet = build_evidence(request, item, fragments)
        status = packet.status
        reasons = list(packet.reasons)
        claims = []
        if packet.claims:
            raw = self._provider.generate(build_payload(request, item, packet, self._corpus))
            validated = validate_generation(raw, packet, item, self._corpus)
            claims = list(validated.claims)
            if validated.rejected_count:
                reasons.append("GENERATION_REJECTED_CLAIMS")
            if not validated.complete and status == "SUPPORTED":
                status = "UNCONFIRMED"
                reasons.append("GENERATION_INCOMPLETE_COVERAGE")
        comment = STATUS_TEXT[status]
        if claims:
            comment += " " + " ".join(claim.text for claim in claims)
        if item.dosage_check.status == "NOT_CHECKED":
            comment += " Проверка дозировки у №2 не выполнялась."
        return ItemResult(
            **item.model_dump(), explanation_status=status, comment=comment, claims=claims,
            review_sources=[citation_for(claim, self._corpus) for claim in packet.review_evidence],
            reasons=reasons, explanation_missing_data=list(packet.missing_data),
        )
