"""Независимые XLSX проходят импорт без подстановки полей и без API."""

import json
from pathlib import Path
import pytest

CASES = Path(__file__).resolve().parents[1] / "excel_cases/multidiagnosis_20261117"


@pytest.mark.parametrize(
    "case", json.loads((CASES / "manifest.json").read_text(encoding="utf-8"))["cases"], ids=lambda c: c["id"]
)
def test_new_excel_import_and_offline_report(case, tmp_path):
    from llm_rag.integration_excel import read_integration_card, write_integration_report
    from llm_rag.integration import _run, IntegrationResources

    path = CASES / "input" / case["file"]
    if case["expected_input_error"]:
        with pytest.raises(ValueError):
            read_integration_card(path)
        return
    request = read_integration_card(path)
    assert request.request_id == case["id"]
    assert len(request.diagnoses) == case["diagnosis_count"]
    assert len(request.links) == len(case["links"])
    if case["id"] == "two":
        assert str(request.purchases[1].quantity) == "0.5"
    result = _run(request, resources=IntegrationResources())
    write_integration_report(result, tmp_path)
    saved = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert saved["human_report"] == result.human_report
    assert all(
        value in (tmp_path / "report.html").read_text(encoding="utf-8")
        for value in ("Проверка заявки", "Диагноз")
    )
