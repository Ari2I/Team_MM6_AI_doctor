"""Чтение прежнего кэша КР без обновления, сетевых клиентов и приватных API библиотеки."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.services.ai_adapter import AIInput


class LocalClinicalSources:
    def __init__(self, cache: Path) -> None:
        from llm_rag.network_policy import NetworkPolicy

        self.cache = cache
        self.network_policy = NetworkPolicy()  # Пустой допуск; сетевых провайдеров здесь нет.
        self.loaded: list[Any] = []
        self.fragment_count = 0

    @staticmethod
    def _require_fresh(metadata: dict[str, Any]) -> None:
        # Сохраняется прежний суточный срок кэша библиотеки; время не подделывается.
        checked = datetime.fromisoformat(metadata["checked_at"])
        if checked.tzinfo is None or not timedelta(0) <= datetime.now(UTC) - checked <= timedelta(
            days=1
        ):
            raise ValueError("Кэш требует проверки актуальности оператором.")

    def candidates(self, code: str, age_group: str | None) -> tuple[Any, ...]:
        from llm_rag.ml2.clinicalrecs import recommendations_for_diagnosis

        metadata = json.loads((self.cache / "catalog-ru.json").read_text(encoding="utf-8"))
        self._require_fresh(metadata)
        if not isinstance(metadata.get("html"), str) or not metadata["html"]:
            raise ValueError("В кэше отсутствует каталог.")
        # Публичный парсер читает неизменяемую копию проверенного кэша. Временное
        # окно его повторной загрузки исключено; исходные даты не изменяются.
        with TemporaryDirectory(prefix="vrach-catalog-") as folder:
            target = Path(folder)
            (target / "catalog-ru.json").write_text(json.dumps(metadata), encoding="utf-8")
            return recommendations_for_diagnosis(
                code, age_group=age_group, cache_dir=target, max_age=timedelta.max
            )

    def load(self, candidate: Any) -> Any:
        from llm_rag.ml2.clinicalrecs import CachedRecommendation, parse_recommendation

        key = sha256(candidate.page_url.encode("utf-8")).hexdigest()[:24]
        html = (self.cache / f"{key}.html").read_text(encoding="utf-8")
        metadata = json.loads((self.cache / f"{key}.json").read_text(encoding="utf-8"))
        self._require_fresh(metadata)
        if sha256(html.encode("utf-8")).hexdigest() != metadata.get("sha256"):
            raise ValueError("Контрольная сумма копии КР не совпадает.")
        cached = CachedRecommendation(
            parse_recommendation(
                html, candidate.page_url, retrieved_at=metadata.get("retrieved_at")
            ),
            "FRESH_CACHE",
            metadata["checked_at"],
        )
        self.loaded.append(cached)
        return cached

    def snapshot(self, pair: AIInput, mnn: str | None) -> Any:
        from llm_rag.evidence_snapshot import EvidenceDocument, EvidenceSnapshot
        from llm_rag.ml2.clinicalrecs import fragments_for_inn

        documents = {}
        for cached in self.loaded:
            rec = cached.recommendation
            for fragment in fragments_for_inn(rec, mnn or ""):
                document = EvidenceDocument(
                    **vars(fragment),
                    diagnosis=pair.diagnosis,
                    inn=mnn,
                    retrieved_at=rec.retrieved_at,
                )
                documents[(fragment.source_id, fragment.fragment_id, fragment.version)] = document
        ordered = [documents[key] for key in sorted(documents)]
        self.fragment_count = len(ordered)
        digest = sha256("".join(d.model_dump_json() for d in ordered).encode()).hexdigest()
        return EvidenceSnapshot(snapshot_version="clinical-" + digest[:16], documents=ordered)
