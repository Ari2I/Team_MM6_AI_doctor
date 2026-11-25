"""Явная ручная сетевая проверка на одной синтетической паре; не часть smoke."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import AISettings  # noqa: E402
from app.schemas.analysis import PairCheck  # noqa: E402
from app.services.ai_adapter import AIAdapter, AIInput  # noqa: E402


def main() -> int:
    started = monotonic()
    status = "AI_UNAVAILABLE"
    adapter = AIAdapter(AISettings(mode="network", corpus_path="ai/examples/corpus.json"))
    try:
        root = Path(__file__).resolve().parents[1]
        # Используется только синтетический комплект, никакого пользовательского файла.
        from openpyxl import load_workbook

        workbook = load_workbook(root / "data/demo/correct.xlsx", read_only=True)
        try:
            name, dosage, quantity, amount, diagnosis = list(workbook.active.values)[1]
        finally:
            workbook.close()
        reference = json.loads((root / "data/reference/demo-v2.json").read_text(encoding="utf-8"))
        drug = next(row for row in reference["drugs"] if row["trade_name"] == name)
        # Категория берётся из demo-справочника, не из предполагаемого ответа LLM.
        diagnosis_id = next(
            row["diagnosis_id"] for row in reference["diagnoses"] if row["name"] == diagnosis
        )
        rule = next(
            row
            for row in reference["rules"]
            if row["mnn"] == drug["mnn"] and row["diagnosis_id"] == diagnosis_id
        )
        result = adapter.analyze(
            AIInput(
                drug_name=name,
                mnn=drug["mnn"],
                diagnosis=diagnosis,
                dosage=dosage,
                quantity=quantity,
                amount=amount,
            ),
            PairCheck(
                category=rule["category"],
                source=rule["source"],
                reference_version=reference["reference_version"],
            ),
        )
        status = result.review_reason.value if result.review_reason else "OK"
    except Exception:
        pass
    print(
        json.dumps(
            {
                "status": status,
                "elapsed_seconds": round(monotonic() - started, 3),
                "tokens": adapter.last_tokens,
                "sent_fields": adapter.last_sent_fields,
            }
        )
    )
    return 0 if status == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
