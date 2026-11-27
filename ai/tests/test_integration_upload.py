"""The CLI and web parser must retain known conditions and explicit links."""

from io import BytesIO

from openpyxl import Workbook
import pytest

from llm_rag import integration_excel


def book_bytes(layout=4, *, group="adult", age="38", edition="359_3"):
    book = Workbook()
    book.remove(book.active)
    if layout == 2:
        rows = {
            "Заявка": [
                ["Поле", "Значение"],
                ["ID заявки", "synthetic"],
                ["ID диагноза", "d1"],
                ["Диагноз", "Синтетический диагноз"],
                ["Код МКБ-10", "J45.9"],
                ["Возраст", age],
                ["Возрастная группа", group],
                ["Редакция КР", edition],
            ]
        }
    else:
        rows = {
            "Заявка": [["Поле", "Значение"], ["ID заявки", "synthetic"]],
            "Диагнозы": [
                integration_excel.HEADERS["Диагнозы"] + ["Возрастная группа", "Редакция КР"],
                ["d1", "Синтетический диагноз", "J45.9", age, group, edition],
                ["d2", "Другой синтетический диагноз", "Z00", age, group, "2_1"],
            ],
        }
    rows["Покупки"] = [
        integration_excel.HEADERS["Покупки"],
        [None] * 6,
        ["i1", "Синтетическое средство", "Исходная форма", 2, "100.125", "RUB"],
    ]
    if layout == 4:
        rows["Связи"] = [integration_excel.HEADERS["Связи"], ["i1", "d1"], ["i1", "d2"]]
    for name, values in rows.items():
        sheet = book.create_sheet(name)
        for row in values:
            sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


@pytest.mark.parametrize("layout", [2, 4])
def test_cli_and_bytes_share_validated_request_and_real_row_numbers(tmp_path, layout):
    content = book_bytes(layout)
    source = tmp_path / "synthetic.xlsx"
    source.write_bytes(content)
    loaded = integration_excel.read_integration_workbook(content)
    assert loaded.request == integration_excel.read_integration_card(source)
    assert loaded.request == integration_excel.read_integration_bytes(content)
    diagnosis = loaded.request.diagnoses[0]
    assert str(diagnosis.conditions.age_years) == "38"
    assert diagnosis.conditions.age_group == "adult"
    assert diagnosis.recommendation_edition == "359_3"
    assert len(loaded.request.links) == (1 if layout == 2 else 2)
    assert loaded.rows_by_item_id == {"i1": 3}
    assert str(loaded.request.purchases[0].amount) == "100.125"


@pytest.mark.parametrize("layout", [2, 4])
def test_conflicting_exact_age_and_group_are_rejected(layout):
    with pytest.raises(ValueError):
        integration_excel.read_integration_bytes(book_bytes(layout, age="8", group="adult"))


@pytest.mark.parametrize("layout", [2, 4])
def test_group_alone_does_not_invent_exact_age(layout):
    request = integration_excel.read_integration_bytes(book_bytes(layout, age=None, group="child"))
    assert request.diagnoses[0].conditions.age_years is None
    assert request.diagnoses[0].conditions.age_group == "child"
