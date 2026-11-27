"""Обезличенное сравнение двух сохранённых запусков. БД только для чтения, AI не вызывается."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _indexed(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    request = value["request"]
    diagnoses = {d["diagnosis_id"]: d for d in request["diagnoses"]}
    purchases = {p["item_id"]: p for p in request["purchases"]}
    outcomes = {
        (p["item_id"], p["diagnosis_id"]): p for p in [*value["pairs"], *value["unprocessed_pairs"]]
    }
    occurrences: Counter[str] = Counter()
    result = {}
    for link in request["links"]:
        diagnosis, purchase = diagnoses[link["diagnosis_id"]], purchases[link["item_id"]]
        # Сравниваются одинаковые факты; отличающиеся возраст/редакция не склеиваются.
        key = sha256(
            json.dumps(
                [
                    purchase["name"],
                    purchase.get("dosage"),
                    diagnosis["name"],
                    diagnosis.get("icd10"),
                    diagnosis.get("recommendation_edition"),
                    diagnosis.get("conditions", {}),
                ],
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        occurrences[key] += 1
        pair = outcomes[link["item_id"], link["diagnosis_id"]]
        decision = pair.get("decision", {})
        category = decision.get("upstream_rule_category") or decision.get("proposed_category")
        if decision.get("classification_basis") == "CONFLICT":
            category = None
        result[f"{key}:{occurrences[key]}"] = {
            "inn": pair.get("inn"),
            "diagnosis": {
                k: diagnosis.get(k)
                for k in (
                    "name",
                    "icd10",
                    "conditions",
                    "recommendation_edition",
                )
            },
            "category": category,
            "decision": decision,
            "reasons": pair.get("reasons", [pair["reason"]] if "reason" in pair else []),
            "classification_diagnostics": pair.get("classification_diagnostics"),
            "citations": pair.get("citations", []),
            "generation_mode": pair.get("generation_mode"),
            "processed": "decision" in pair,
        }
    return result


def comparison(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old, new = _indexed(before), _indexed(after)
    rows = []
    for number, key in enumerate(sorted(set(old) | set(new)), 1):
        rows.append({"number": number, "before": old.get(key), "after": new.get(key)})
    return {
        "schema_version": "category-comparison.1",
        "pairs": rows,
        "matched_pairs": len(set(old) & set(new)),
        "before_only": len(set(old) - set(new)),
        "after_only": len(set(new) - set(old)),
        "before_manifest": before["manifest"],
        "after_manifest": after["manifest"],
        "notice": "Техническое сравнение. Правильность категории и достаточность цитат "
        "сверяет специалист.",
    }


def write_private(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def export(before_id: UUID, after_id: UUID, output: Path) -> dict[str, Any]:
    from sqlalchemy import text

    from app.db.session import SessionLocal
    from app.models import ApplicationAIHistory, ApplicationAIResult

    # Текущий результат или сохранённая версия истории (UUID строки истории).
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        records = []
        for identifier in (before_id, after_id):
            stored = session.get(ApplicationAIResult, identifier)
            if stored is None:
                stored = session.get(ApplicationAIHistory, identifier)
            if stored is None:
                raise ValueError("Сохранённый отчёт не найден.")
            records.append(stored)
        value = comparison(records[0].result, records[1].result)
        value["revisions"] = {
            side: {"backend": record.backend_revision, "ai": record.ai_revision}
            for side, record in zip(("before", "after"), records, strict=True)
        }
        write_private(output, value)
        return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=UUID)
    parser.add_argument("--after", required=True, type=UUID)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = export(args.before, args.after, args.output)
    except Exception as error:
        print("Сравнение не сохранено. Тип ошибки:", type(error).__name__)
        return 1
    print(
        "Сравнение сохранено; совпавших пар:",
        result["matched_pairs"],
        "; только до:",
        result["before_only"],
        "; только после:",
        result["after_only"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
