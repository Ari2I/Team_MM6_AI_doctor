"""Сопоставление формы не является проверкой схемы приёма."""

import pytest


@pytest.mark.parametrize(
    "input,forms,status",
    [
        ("100 мг", ["таблетки 50 мг"], "MISMATCH"),
        ("0.05 г", ["таблетки 50 мг"], "MATCH"),
        ("500 мкг", ["0,5 мг"], "MATCH"),
        ("10 мг/мл", ["50 мг/5 мл"], "MATCH"),
        ("100 мкг/доза", ["100 мкг/доза"], "MATCH"),
        ("100 мкг/доза", ["200 мкг/доза"], "MISMATCH"),
        ("50 мг", ["50 мг", "100 мг"], "MATCH"),
        (None, ["50 мг"], "UNKNOWN"),
        ("50 мг", [], "UNKNOWN"),
        ("1/2 таблетки", ["50 мг"], "UNKNOWN"),
        ("50 мг", ["неизвестная форма"], "UNKNOWN"),
        ("50 мг", ["50 мг/мл"], "UNKNOWN"),
        ("50 мг + 10 мг", ["50 мг + 20 мг"], "UNKNOWN"),
        ("1 050 мг", ["50 мг"], "UNKNOWN"),
        ("50 мг/2 таблетки", ["50 мг"], "UNKNOWN"),
    ],
)
def test_form_matching(input, forms, status):
    from llm_rag.ml2.form_matching import match_form

    result = match_form(input, forms)
    assert result.status == status
    assert result.reason
