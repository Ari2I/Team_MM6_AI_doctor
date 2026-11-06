"""Проверки границ диагнозов из XLSX."""

from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl import Workbook

from app.services.excel_parser import parse_excel
from tests.backend.test_demo import client


def test_purchase_uses_only_its_own_block():
    book = Workbook()
    sheet = book.active
    for row in (
        ("Диагноз (из заключения)", "Сумма", "Торговое наименование", "Дозировка", "Количество"),
        (None, 10, "Препарат до диагноза"),
        ("G43.9", 20, "Препарат X"),
        (None, 30, "Препарат Y"),
        (None, None, None),
        ("K25.9", None, None),
        (None, 40, "Препарат X"),
        ("Неразрешённый диагноз", 50, "Препарат Z"),
    ):
        sheet.append(row)
    content = BytesIO()
    book.save(content)

    parsed = parse_excel(content.getvalue(), demo=True, diagnosis_blocks=True)
    assert parsed.diagnosis_blocks == {0: "Диагноз не указан", 3: "G43.9", 6: "K25.9", 8: "Неразрешённый диагноз"}
    assert [(item.row_number, parsed.item_blocks[item.row_number]) for item in parsed.items] == [
        (2, 0), (3, 3), (4, 3), (7, 6), (8, 8),
    ]
    assert parsed.items[3].diagnosis == "K25.9"
    assert parsed.items[3].original_cells["diagnosis"] is None


def test_official_excel_uses_cached_amount_and_preserves_formula():
    book = Workbook()
    book.active.append(["Торговое наименование", "Дозировка", "Количество", "Сумма", "Диагноз (из заключения)"])
    book.active.append(["Первый", None, 1, "=4+6", "G43.9"])
    book.active.append(["Второй", None, 1, "=2+3"])
    content = BytesIO()
    book.save(content)
    patched = BytesIO()
    with ZipFile(content) as source, ZipFile(patched, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                root = ElementTree.fromstring(data)
                ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                cell = root.find(f".//{ns}c[@r='D2']")
                cell.find(ns + "v").text = "10"
                data = ElementTree.tostring(root)
            target.writestr(name, data)
    parsed = parse_excel(patched.getvalue(), demo=True, diagnosis_blocks=True)
    assert parsed.items[0].amount == 10
    assert parsed.items[0].original_cells["amount"] == "=4+6"
    assert parsed.items[1].amount is None
    assert any(issue.code == "AMOUNT_FORMULA_NO_CACHE" and issue.row == 3 for issue in parsed.issues)


def test_official_upload_reports_only_block_checks(client, monkeypatch, tmp_path):
    from app.api import applications
    from tests.backend.test_official_reference import make_reference

    snapshot = tmp_path / "reference.json"
    snapshot.write_text(make_reference().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(applications, "official_path", lambda: snapshot)
    book = Workbook()
    for row in (
        ("Торговое наименование", "Дозировка", "Количество", "Сумма", "Диагноз (из заключения)"),
        ("Препарат до диагноза", None, 1, 10, None),
        ("Независимый тестовый препарат", None, 1, 20, "G43.9"),
        ("Независимый тестовый препарат", None, 1, 30, None),
        (None, None, None, None, "K25.9"),
        ("Независимый тестовый препарат", None, 1, 40, None),
    ):
        book.active.append(row)
    data = BytesIO()
    book.save(data)
    response = client.post("/api/applications", files={"file": ("новая заявка.xlsx", data.getvalue())},
                           data={"reference": "official", "mode": "offline"})
    assert response.status_code == 201, response.text
    result = response.json()
    by_id = {item["item_id"]: item["row_number"] for item in result["items"]}
    diagnoses = {item["diagnosis_id"]: item["name"] for item in result["diagnoses"]}
    assert [(by_id[check["item_id"]], diagnoses[check["diagnosis_id"]])
            for check in result["checks"]] == [(2, "Диагноз не указан"), (3, "G43.9"), (4, "G43.9"), (6, "K25.9")]
    assert result["checks"][0]["reason_code"] == "OFFICIAL_DIAGNOSIS_MISSING"
    assert [row["amounts"]["undetermined"] for row in result["summary_by_diagnosis"]] == ["10.00", "50.00", "40.00"]
    assert result["known_total"] == "100.00"
    assert client.get(f'/api/applications/{result["application_id"]}').json() == result

def test_new_workbook_uses_b2b_with_public_material_only(client, monkeypatch, tmp_path):
    from app.api import applications
    from app.services import official_explanation
    from tests.backend.test_official_reference import make_reference

    calls = []

    class FakeProvider:
        def __init__(self, *args, **kwargs):
            assert kwargs["scope"] == "GIGACHAT_API_B2B"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def select_official_explanations(self, rows):
            calls.extend(rows)
            return {"selections": {str(r["row"]): 0 for r in rows}, "summary_rows": [rows[0]["row"]]}

    monkeypatch.setattr(official_explanation, "GigaChatProvider", FakeProvider)
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "synthetic")
    monkeypatch.delenv("GIGACHAT_ACCESS_TOKEN", raising=False)
    snapshot = tmp_path / "reference.json"
    snapshot.write_text(make_reference().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(applications, "official_path", lambda: snapshot)
    book = Workbook()
    book.active.append(["Торговое наименование", "Дозировка", "Количество", "Сумма", "Диагноз (из заключения)"])
    book.active.append(["Независимый тестовый препарат", "Private dosage text", 3, 543.21, "G43.9"])
    data = BytesIO()
    book.save(data)
    response = client.post("/api/applications", files={"file": ("неизвестный файл.xlsx", data.getvalue())})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["model_attempted"] is True
    assert result["effective_mode"] == "gigachat"
    assert result["checks"][0]["model_highlight"]["url"].startswith("https://grls.")
    assert result["model_summary"]
    assert "Private dosage text" not in repr(calls) and "543.21" not in repr(calls)
    assert "Независимый тестовый препарат" not in repr(calls)
    assert client.get(f'/api/applications/{result["application_id"]}').json() == result

def test_upload_asks_for_sheet_and_column_choice(client, monkeypatch, tmp_path):
    from app.api import applications
    from tests.backend.test_official_reference import make_reference

    snapshot = tmp_path / "reference.json"
    snapshot.write_text(make_reference().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(applications, "official_path", lambda: snapshot)
    book = Workbook()
    book.active.title = "Первая"
    for sheet in (book.active, book.create_sheet("Покупки")):
        sheet.append(["Торговое наименование", "Дозировка", "Количество", "Сумма", "Сумма, руб.", "Диагноз"])
        sheet.append(["Независимый тестовый препарат", None, 1, 10, 20, "G43.9"])
    data = BytesIO()
    book.save(data)
    payload = {"reference": "official", "mode": "offline"}

    def post():
        return client.post("/api/applications", files={"file": ("заявка.xlsx", data.getvalue())}, data=payload)

    response = post()
    assert response.status_code == 409
    assert [row["name"] for row in response.json()["detail"]["layout"]["sheets"]] == ["Первая", "Покупки"]
    payload["sheet"] = "Покупки"
    response = post()
    assert response.status_code == 409
    assert [row["index"] for row in response.json()["detail"]["layout"]["columns"]["amount"]] == [3, 4]
    payload["columns"] = '{"amount": 4}'
    response = post()
    assert response.status_code == 201
    assert response.json()["sheet_name"] == "Покупки"
    assert response.json()["known_total"] == "20.00"


def test_rule_category_and_companion_do_not_cross_diagnosis_blocks(client, monkeypatch, tmp_path):
    from app.api import applications
    from app.services.official_reference import OfficialRule, RegistryEntry
    from tests.backend.test_official_reference import make_reference, SOURCE

    base = make_reference()
    evidence = base.evidence[0].model_copy(update={"polarity": "supports"})
    rule = OfficialRule(
        rule_id="R-1", revision="1", evidence_id=evidence.entry_id, category="B",
        approval_reference="Синтетическое утверждение", required_purchases=["компонент а"],
    )
    companion = RegistryEntry(registration="TEST-4", trade_name="Второй препарат",
                              mnn="компонент а", source=SOURCE)
    snapshot = tmp_path / "reference.json"
    snapshot.write_text(base.model_copy(update={
        "evidence": (evidence,), "rules": (rule,), "registrations": (*base.registrations, companion),
    }).model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(applications, "official_path", lambda: snapshot)
    book = Workbook()
    book.active.append(["Торговое наименование", "Дозировка", "Количество", "Сумма", "Диагноз (из заключения)"])
    book.active.append(["Независимый тестовый препарат", None, 1, 30, "G43.9"])
    book.active.append(["Второй препарат", None, 1, 10])
    book.active.append(["Независимый тестовый препарат", None, 1, 20, "G43.9"])
    data = BytesIO()
    book.save(data)
    response = client.post("/api/applications", files={"file": ("заявка.xlsx", data.getvalue())},
                           data={"mode": "offline"})
    assert response.status_code == 201
    result = response.json()
    assert [check["category"] for check in result["checks"]] == ["Б", None, None]
    assert result["checks"][2]["reason_code"] == "OFFICIAL_RULE_CONDITION_MISSING"
    assert result["amounts_total"] == {"А": "0.00", "Б": "30.00", "В": "0.00", "undetermined": "30.00"}
    assert result["known_total"] == "60.00"
    route = f'/api/applications/{result["application_id"]}'
    assert "Правила R-1@1" in client.get(route + "/report.html").text
