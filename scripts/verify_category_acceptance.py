"""Контроль категорий по сохранённому результату: без вызова AI и печати заявки."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_category_comparison import write_private  # noqa: E402

INDEX = Path("/var/lib/vrach/rag/indexes/official-embeddings.json")
FAILURES = {
    "AI_TIMEOUT",
    "AI_UNAVAILABLE",
    "AI_INVALID_RESPONSE",
    "SOURCE_ERROR",
    "DIAGNOSIS_MISMATCH",
    "NETWORK_POLICY_DENIED",
    "BUDGET_EXHAUSTED",
    "CONTEXT_LIMIT",
    "INTERNAL_ERROR",
    "VERSION_MISMATCH",
}
SAFE_CODES = {
    "INCOMPLETE_RESULT",
    "PAIR_NOT_ACCEPTED",
    "CATEGORY_NOT_ESTABLISHED",
    "MODEL_OR_EMBEDDINGS_NOT_CONFIRMED",
    "NO_FRESH_RELEVANT_EMBEDDINGS",
    "REPORT_NOT_FOUND",
    "SOURCE_REVISION_MISMATCH",
    "MISSING_ARGUMENTS",
    "INDEX_VERSION",
    "INDEX_MODEL",
    "INDEX_VECTORS",
    "STORED_CHECKS_MISMATCH",
}


def assess(result: dict, index: dict, before: set[str], *, require_categories: bool) -> dict:
    if result["completeness"] != "COMPLETE" or result["unprocessed_pairs"] or not result["pairs"]:
        raise ValueError("INCOMPLETE_RESULT")
    for pair in result["pairs"]:
        if (
            pair["generation_mode"] != "MODEL"
            or FAILURES.intersection(pair["reasons"])
            or not pair["citations"]
            or pair.get("classification_diagnostics") is None
        ):
            raise ValueError("PAIR_NOT_ACCEPTED")
        decision = pair["decision"]
        category = decision.get("upstream_rule_category") or decision.get("proposed_category")
        if require_categories and (not category or decision["classification_basis"] == "CONFLICT"):
            raise ValueError("CATEGORY_NOT_ESTABLISHED")
    info = result.get("retrieval_info") or {}
    if (
        not result["usage"]["calls"]
        or info.get("method") != "vector"
        or not info.get("embedding_revision")
        or info["embedding_revision"].startswith("unverified:")
    ):
        raise ValueError("MODEL_OR_EMBEDDINGS_NOT_CONFIRMED")
    expected = set()
    for doc in result["evidence_snapshot"]["documents"]:
        prefix = f"МНН: {doc['inn']}. Диагноз: {doc['diagnosis']}."
        expected.add(sha256(prefix.encode()).hexdigest())
        expected.add(sha256((prefix + " " + doc["text"]).encode()).hexdigest())
    fresh = set(index["vectors"]) - before
    if not fresh.intersection(expected):
        raise ValueError("NO_FRESH_RELEVANT_EMBEDDINGS")
    from scripts.verify_network_acceptance import verify_index

    verify_index(result, index, fresh)
    counts = Counter(
        (p["decision"].get("upstream_rule_category") or p["decision"].get("proposed_category"))
        if p["decision"]["classification_basis"] != "CONFLICT"
        else None
        for p in result["pairs"]
    )
    if None in counts:
        counts["не определена"] = counts.pop(None)
    return {
        "pair_count": len(result["pairs"]),
        "category_counts": dict(counts),
        "fresh_relevant_vectors": len(fresh.intersection(expected)),
    }


def verify(identifier: UUID, baseline: Path, output: Path, *, require_categories: bool) -> dict:
    from llm_rag.integration_contracts import IntegrationResult
    from llm_rag.ml2.classifier import policy_fingerprint
    from sqlalchemy import select, text

    from app.config import AISettings
    from app.db.session import SessionLocal
    from app.models import ApplicationAIResult, ApplicationItem, ItemCheck

    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        stored = session.get(ApplicationAIResult, identifier)
        if stored is None:
            raise ValueError("REPORT_NOT_FOUND")
        value = IntegrationResult.model_validate(stored.result).model_dump(mode="json")
        settings = AISettings()
        if (
            stored.backend_revision != settings.backend_revision
            or stored.ai_revision != settings.library_revision
            or (value["manifest"]["policy"]["version"], value["manifest"]["policy"]["sha256"])
            != policy_fingerprint()
        ):
            raise ValueError("SOURCE_REVISION_MISMATCH")
        index = json.loads(INDEX.read_bytes())
        result = assess(
            value,
            index,
            set(json.loads(baseline.read_bytes())),
            require_categories=require_categories,
        )
        actual = {
            (str(p.item_id), str(p.diagnosis_id)): p.category.value if p.category else None
            for p in session.scalars(
                select(ItemCheck)
                .join(ApplicationItem)
                .where(
                    ApplicationItem.application_id == identifier,
                )
            )
        }
        expected = {
            (p["item_id"], p["diagnosis_id"]): (
                p["decision"].get("upstream_rule_category")
                or p["decision"].get("proposed_category")
            )
            if p["decision"]["classification_basis"] != "CONFLICT"
            else None
            for p in value["pairs"]
        }
        if actual != expected:
            raise ValueError("STORED_CHECKS_MISMATCH")
        write_private(
            output,
            {
                "result": value,
                "backend_revision": stored.backend_revision,
                "ai_revision": stored.ai_revision,
                "index_sha256": sha256(INDEX.read_bytes()).hexdigest(),
                "acceptance": result,
            },
        )
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", type=UUID)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-categories", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.baseline_only:
            index = json.loads(INDEX.read_bytes()) if INDEX.exists() else {}
            write_private(args.baseline, sorted(index.get("vectors", {})))
            print("Исходные хэши векторов сохранены; модель не вызывалась.")
            return 0
        if args.application_id is None or args.output is None:
            raise ValueError("MISSING_ARGUMENTS")
        source = Path(__file__).resolve().parents[1] / "ai/src"
        if source.is_dir():
            sys.path.insert(0, str(source))
        result = verify(
            args.application_id,
            args.baseline,
            args.output,
            require_categories=args.require_categories,
        )
    except Exception as error:
        reason = (
            error.args[0]
            if error.args and isinstance(error.args[0], str) and error.args[0] in SAFE_CODES
            else type(error).__name__
        )
        print("Контроль не подтверждён. Причина:", reason)
        return 1
    print("Модельный отчёт, свежие векторы, сохранённые основания и ревизии: OK.")
    print(
        "Пар:",
        result["pair_count"],
        "; категории:",
        json.dumps(result["category_counts"], ensure_ascii=False),
    )
    print("Правильность букв и достаточность цитат требуют сверки по сохранённому файлу.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
