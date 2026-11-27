"""Границы стоимости одной покупки на входах JSON, Python и XLSX."""

from decimal import Decimal
import json
from xml.etree import ElementTree
from zipfile import ZipFile

import jsonschema
from openpyxl import load_workbook
from pydantic import ValidationError
import pytest

from llm_rag.__main__ import exported_schemas
from llm_rag.contracts import AnalysisRequest
from llm_rag.explanation_contracts import PurchaseAmount
from llm_rag.integration_contracts import IntegrationRequest
from llm_rag.integration_excel import read_integration_card
from tests.helpers import ROOT, request_data
from tests.test_integration import request


@pytest.mark.parametrize("amount,valid", [
    ("0", True), ("0.001", True), ("999999999999.999", True),
    ("1000000000000", False), ("1.0000", False), ("1e-1000050", False),
    ("0e+1000050", False), ("0." + "0" * 2000, False),
])
def test_both_json_request_contracts_and_schemas_enforce_money_limits(amount, valid):
    legacy = request_data()
    legacy["items"][0]["original"]["amount"] = amount
    integration = request(1).model_dump(mode="json")
    integration["purchases"][0]["amount"] = amount
    schemas = exported_schemas()
    for data, model, key in ((legacy, AnalysisRequest, "input"),
                             (integration, IntegrationRequest, "integration_input")):
        assert jsonschema.Draft202012Validator(schemas[key]).is_valid(data) == valid
        if valid:
            model.model_validate_json(json.dumps(data))
        else:
            with pytest.raises(ValidationError):
                model.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("amount", ["1e-1000050", "0e+1000050", "1000000000000", "1.0000"])
def test_decimal_amount_is_checked_before_explanation_totals(amount):
    with pytest.raises(ValidationError):
        PurchaseAmount(request_id="r", item_id="i", amount=Decimal(amount), currency="RUB")


@pytest.mark.parametrize("source", [
    "excel_input/01_Подходящие_препараты.xlsx",
    "excel_cases/multidiagnosis_20261117/input/single.xlsx",
])
@pytest.mark.parametrize("amount,valid", [
    ("999999999999.999", True), (0.001, True),
    ("1000000000000", False), ("0.0001", False), ("1e+1000050", False),
])
def test_xlsx_purchase_amount_boundaries(tmp_path, source, amount, valid):
    path = tmp_path / "card.xlsx"
    book = load_workbook(ROOT / source)
    try:
        book["Покупки"]["E2"] = amount
        book.save(path)
    finally:
        book.close()
    if valid:
        assert read_integration_card(path).purchases[0].amount == Decimal(str(amount))
    else:
        with pytest.raises(ValueError):
            read_integration_card(path)


@pytest.mark.parametrize("source,sheet", [
    ("excel_input/01_Подходящие_препараты.xlsx", "sheet2.xml"),
    ("excel_cases/multidiagnosis_20261117/input/single.xlsx", "sheet3.xml"),
])
def test_excel_checks_numeric_cell_before_float_rounding(tmp_path, source, sheet):
    path = tmp_path / "precision.xlsx"
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with ZipFile(ROOT / source) as original, ZipFile(path, "w") as output:
        for item in original.infolist():
            content = original.read(item)
            if item.filename == f"xl/worksheets/{sheet}":
                tree = ElementTree.fromstring(content)
                cell = tree.find(f'.//{ns}c[@r="E2"]')
                cell.clear()
                cell.set("r", "E2")
                ElementTree.SubElement(cell, ns + "v").text = "1.00100000000000001"
                content = ElementTree.tostring(tree)
            output.writestr(item, content)
    with pytest.raises(ValueError, match="сумма"):
        read_integration_card(path)
