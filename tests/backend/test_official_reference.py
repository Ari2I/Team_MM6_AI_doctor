"""Проверки независимого импорта и поиска по официальной схеме."""

from datetime import date
from pathlib import Path

from openpyxl import Workbook
import pytest

from app.services.official_reference import (
    OfficialIndex, OfficialSnapshot, RegistryEntry, EvidenceEntry, CompositionEntry, Source,
    import_grls, load_official,
)

DAY = date(2026, 11, 6)
SOURCE = Source(
    title="Тест записи об источнике", url="https://grls.minzdrav.gov.ru/grls.aspx",
    version="fixture", location="тестовая строка", retrieved_at=DAY,
    quote="Синтетическая фикстура проверки контракта",
)


def make_reference():
    return OfficialSnapshot(
        version="fixture-v1", as_of=DAY,
        registrations=[
            RegistryEntry(registration="TEST-1", trade_name="Независимый тестовый препарат",
                          mnn="компонент а + компонент б", form="таблетки", source=SOURCE),
            RegistryEntry(registration="TEST-2", trade_name="Неоднозначный тестовый",
                          mnn="компонент а", source=SOURCE),
            RegistryEntry(registration="TEST-3", trade_name="Неоднозначный тестовый",
                          mnn="компонент б", source=SOURCE),
        ],
        evidence=[EvidenceEntry(
            code="G43.9", diagnosis="Тестовый диагноз", mnn="компонент б+компонент а",
            kind="standard", conditions="Взрослые", source=SOURCE,
        )],
        compositions=[CompositionEntry(registration="TEST-1", form="таблетки",
                                       source=SOURCE)],
    )


def test_grls_workbook_and_inactive_rows(tmp_path):
    path = tmp_path / "registry.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["Заголовок выгрузки"])
    sheet.append(["Номер регистрационного удостоверения", "Торговое наименование лекарственного препарата",
                  "Международное непатентованное или химическое наименование", "Формы выпуска",
                  "Статус РУ", "Дата аннулирования регистрацион- ного удостоверения"])
    sheet.append(["TEST-1", "Одно название", "МНН 1", "таблетки", "Действующий"])
    sheet.append(["TEST-2", "Другое название", "МНН 2", "капсулы", "Исключенный"])
    sheet.append(["TEST-3", "Аннулированный", "МНН 3", "таблетки", "Действующий", "05.11.2026"])
    book.save(path)

    records = import_grls(path, as_of=DAY, registry_url=SOURCE.url)
    assert [(row.registration, row.trade_name, row.mnn) for row in records] == [
        ("TEST-1", "Одно название", "МНН 1"),
    ]


def test_lookup_uses_normalized_keys_and_never_infers_a_category():
    index = OfficialIndex(make_reference())
    resolved = index.resolve("  независимый Тестовый препарат® ")
    assert resolved.mnn == "компонент а + компонент б"
    result = index.lookup(resolved, "g43.9")
    assert result["reason_code"] == "OFFICIAL_LINK_REQUIRES_REVIEW"
    assert result["review_sources"][0]["url"] == SOURCE.url
    assert result["relationship_found"]
    assert result.get("category") is None
    assert resolved.registrations[0].registration == "TEST-1"
    assert index.lookup(resolved, "G43.0")["reason_code"] == "OFFICIAL_DIAGNOSIS_NOT_INDEXED"


def test_unmatched_and_ambiguous_lookups_remain_unresolved():
    index = OfficialIndex(make_reference())
    assert index.resolve("Неоднозначный тестовый").candidates == ["компонент а", "компонент б"]
    typo = index.resolve("Независимый тестовый препара")
    assert typo.mnn is None and typo.candidates
    assert index.lookup(typo, "G43.9")["reason_code"] == "OFFICIAL_DRUG_UNRESOLVED"
    assert index.lookup(index.resolve("произвольное название 58304"), "G43.9")[
        "reason_code"
    ] == "OFFICIAL_DRUG_NOT_FOUND"


def test_code_and_known_diagnosis_name_must_agree():
    snapshot = make_reference()
    prototype = snapshot.evidence[0]
    group = prototype.model_copy(update={"code": "G43", "diagnosis": "Мигрень", "scope": "group"})
    other = prototype.model_copy(update={"code": "K25", "diagnosis": "Язва желудка", "scope": "group"})
    index = OfficialIndex(snapshot.model_copy(update={"evidence": (group, other)}))
    drug = index.resolve("Независимый тестовый препарат")
    assert index.lookup(drug, "G43.9 Мигрень")["reason_code"] == "OFFICIAL_GROUP_LINK_REQUIRES_REVIEW"
    assert index.lookup(drug, "G43.9 Язва желудка")["reason_code"] == "OFFICIAL_DIAGNOSIS_CONFLICT"
    assert index.lookup(drug, "G43.AAAA")["reason_code"] == "OFFICIAL_DIAGNOSIS_NOT_INDEXED"


def test_load_rejects_source_outside_official_domains(tmp_path):
    path = tmp_path / "reference.json"
    path.write_text(make_reference().model_dump_json().replace(
        "grls.minzdrav.gov.ru", "grls.minzdrav.gov.ru.example.org"
    ), encoding="utf-8")
    with pytest.raises(ValueError, match="официального"):
        load_official(path)
