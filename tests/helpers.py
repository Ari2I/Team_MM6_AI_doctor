from copy import deepcopy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def corpus_data():
    return json.loads((ROOT / "examples/corpus.json").read_text(encoding="utf-8"))


def request_data():
    return {
        "schema_version": "1.0", "request_id": "DEMO-001", "diagnosis": "Демо-диагноз",
        "is_demo": True, "items": [item_data()],
    }


def item_data(item_id="row-1", category="А"):
    return deepcopy({
        "item_id": item_id,
        "original": {"trade_name": "Демо-А", "dosage": "10 демо-ед.",
                     "quantity": 2, "amount": "125.50", "currency": "RUB"},
        "inn": "демо-вещество-а", "category": category,
        "classification_reason": "Основная терапия в тестовом перечне.",
        "dosage_check": {"status": "NOT_CHECKED", "reason": None, "missing_data": []},
        "source_refs": ["master-demo"], "upstream_status": "PROCESSED", "missing_data": [],
    })
