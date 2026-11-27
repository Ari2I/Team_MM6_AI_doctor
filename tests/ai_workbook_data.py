"""Только синтетические книги; совпадают с публичными шаблонами AI."""

from io import BytesIO

from openpyxl import Workbook


def workbook_bytes(layout=4, *, age="38", group="adult", links=None, formula=False):
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
                ["Редакция КР", "359_3"],
            ]
        }
    else:
        rows = {
            "Заявка": [["Поле", "Значение"], ["ID заявки", "synthetic"]],
            "Диагнозы": [
                [
                    "ID диагноза",
                    "Диагноз",
                    "Код МКБ-10",
                    "Возраст",
                    "Возрастная группа",
                    "Редакция КР",
                ],
                ["d1", "Синтетический диагноз", "J45.9", age, group, "359_3"],
                ["d2", "Другой синтетический диагноз", "Z00", age, group, "2_1"],
            ],
        }
    rows["Покупки"] = [
        ["ID позиции", "Препарат", "Дозировка", "Количество", "Сумма", "Валюта"],
        [None] * 6,
        [
            "i1",
            "Синтетическое средство",
            "Исходная форма",
            2,
            "=1+1" if formula else "100.125",
            "RUB",
        ],
        ["i2", "Второе синтетическое средство", "Исходная форма", 3, "200", "RUB"],
    ]
    if layout == 4:
        rows["Связи"] = [
            ["ID позиции", "ID диагноза"],
            *(
                links
                if links is not None
                else [
                    ["i1", "d1"],
                    ["i1", "d2"],
                    ["i2", "d2"],
                ]
            ),
        ]
    for name, values in rows.items():
        sheet = book.create_sheet(name)
        for row in values:
            sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()
