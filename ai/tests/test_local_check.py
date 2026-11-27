"""Проверка обеих раскладок XLSX и единой точки входа."""

import json
from pathlib import Path
from shutil import copyfile

import pytest
from openpyxl.worksheet._read_only import ReadOnlyWorksheet

import check_local
from llm_rag.integration import IntegrationResources, analyze_request
from llm_rag.integration_excel import read_integration_card
from llm_rag.human_report import render_human_report

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "path",
    [
        "excel_input/01_Подходящие_препараты.xlsx",
        "excel_cases/multidiagnosis_20261117/input/two.xlsx",
    ],
)
def test_local_and_backend_share_request_and_result(monkeypatch, tmp_path, path):
    calls = []

    def prepare(request, verify_ssl_certs, options, start):
        calls.append((request, verify_ssl_certs, options))
        return IntegrationResources()

    monkeypatch.setattr("llm_rag.runtime.prepare_resources", prepare)
    assert check_local.analyze_request is analyze_request
    source = ROOT / path
    direct = analyze_request(read_integration_card(source))
    assert check_local.main([str(source), "--output", str(tmp_path)]) == 1
    saved = json.loads((tmp_path / "result.json").read_text(encoding="utf8"))
    assert saved["pairs"] == direct.model_dump(mode="json")["pairs"]
    assert (tmp_path / "report.html").read_text(encoding="utf8") == render_human_report(saved["human_report"])
    assert calls[0][0] == calls[1][0]
    assert calls[0][1] is True and calls[1][1] is True
    assert calls[0][2] == calls[1][2]


def test_invalid_card_stops_before_sources(monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        pytest.fail("Обращение к источникам до проверки входа")

    monkeypatch.setattr(check_local, "analyze_request", unexpected)
    invalid = ROOT / "excel_cases/multidiagnosis_20261117/input/invalid_link.xlsx"
    assert check_local.main([str(invalid), "--output", str(tmp_path)]) == 2
    assert not list(tmp_path.iterdir())


def test_default_batch_checks_all_cards_even_after_invalid_input(monkeypatch, tmp_path, capsys):
    inputs = tmp_path / "excel_input"
    inputs.mkdir()
    (inputs / "00_invalid.xlsx").write_bytes(b"invalid workbook")
    (inputs / "~$locked.xlsx").touch()
    (inputs / "README.md").touch()
    copyfile(ROOT / "excel_input/01_Подходящие_препараты.xlsx", inputs / "00_shared_strings.xlsx")
    copyfile(ROOT / "excel_input/01_Подходящие_препараты.xlsx", inputs / "01_card.xlsx")
    copyfile(ROOT / "excel_cases/multidiagnosis_20261117/input/two.xlsx", inputs / "02_card.XLSX")
    monkeypatch.setattr(check_local, "ROOT", tmp_path)

    original_iter_rows = ReadOnlyWorksheet.iter_rows

    def fail_once(self, *args, **kwargs):
        # Имитируем ошибку openpyxl при первом ленивом чтении строк.
        monkeypatch.setattr(ReadOnlyWorksheet, "iter_rows", original_iter_rows)
        raise IndexError("Недоступна общая строка")

    monkeypatch.setattr(ReadOnlyWorksheet, "iter_rows", fail_once)

    def prepare(request, verify_ssl_certs, options, start):
        assert verify_ssl_certs is True
        return IntegrationResources()

    monkeypatch.setattr("llm_rag.runtime.prepare_resources", prepare)
    assert check_local.main([]) == 2
    errors = capsys.readouterr().err
    assert "00_invalid.xlsx: ошибка чтения XLSX" in errors
    assert "00_shared_strings.xlsx: ошибка чтения XLSX" in errors

    (run,) = (tmp_path / "excel_results").iterdir()
    assert {folder.name for folder in run.iterdir()} == {"01_card.xlsx", "02_card.XLSX"}
    for name in ("01_card.xlsx", "02_card.XLSX"):
        saved = json.loads((run / name / "result.json").read_text(encoding="utf8"))
        assert (run / name / "report.html").read_text(encoding="utf8") == render_human_report(
            saved["human_report"]
        )
