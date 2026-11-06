"""Создание синтетического примера с заголовком на третьей строке."""

from pathlib import Path

from openpyxl import Workbook


if __name__ == "__main__":
    target = Path(__file__).with_name("demo.xlsx")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Синтетическая заявка"
    sheet.append(["Синтетическая заявка для защиты"])
    sheet.append([])
    sheet.append(["Торговое наименование", "Дозировка", "Количество", "Сумма", "Диагноз",
                  "Сумма, руб."])
    sheet.append(["Норваск", None, None, 850,
                  "Артериальная гипертензия; Сахарный диабет 2 типа"])
    sheet.append(["Глюкофаж", None, None, 100])
    sheet.append(["Норваск", None, None, 30, "Артериальная гипертензия"])
    sheet.append(["Тестовый препарат X", None, None, 20])
    workbook.save(target)
