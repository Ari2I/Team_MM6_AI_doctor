"""Сравнение не подменяет вход и сохраняет цитаты без лишних данных покупки."""

import json
import stat
from copy import deepcopy

import pytest

from scripts.export_category_comparison import comparison, write_private

pytestmark = pytest.mark.usefixtures("no_network")


def sample():
    return {
        "request": {
            "request_id": "PRIVATE_REQUEST",
            "diagnoses": [
                {
                    "diagnosis_id": "PRIVATE_DIAGNOSIS",
                    "name": "Синтетический диагноз",
                    "icd10": "Z00",
                    "conditions": {"age_years": "38", "age_group": "adult"},
                    "recommendation_edition": "1_1",
                },
            ],
            "purchases": [
                {
                    "item_id": "PRIVATE_ITEM",
                    "name": "PRIVATE_TRADE_NAME",
                    "dosage": "PRIVATE_DOSAGE",
                    "amount": "123.45",
                }
            ],
            "links": [{"item_id": "PRIVATE_ITEM", "diagnosis_id": "PRIVATE_DIAGNOSIS"}],
        },
        "pairs": [
            {
                "item_id": "PRIVATE_ITEM",
                "diagnosis_id": "PRIVATE_DIAGNOSIS",
                "inn": "Синтетическое вещество",
                "decision": {"proposed_category": "А"},
                "citations": [{"quote": "Дословно: запятая, тире —\nи новая строка."}],
            }
        ],
        "unprocessed_pairs": [],
        "manifest": {},
    }


def test_private_export_omits_purchase_fields_and_preserves_exact_quote(tmp_path, capsys):
    before = sample()
    after = deepcopy(before)
    after["request"]["request_id"] = "OTHER_REQUEST"
    result = comparison(before, after)
    assert result["matched_pairs"] == 1
    assert result["pairs"][0]["after"]["citations"] == before["pairs"][0]["citations"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "PRIVATE_" not in serialized and "123.45" not in serialized
    path = tmp_path / "private.json"
    write_private(path, result)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == result
    with pytest.raises(FileExistsError):
        write_private(path, result)
    assert capsys.readouterr().out == ""


def test_different_clinical_facts_cannot_be_reported_as_same_pair():
    before, after = sample(), sample()
    after["request"]["diagnoses"][0]["conditions"]["age_years"] = "8"
    result = comparison(before, after)
    assert result["matched_pairs"] == 0
    assert result["before_only"] == result["after_only"] == 1
