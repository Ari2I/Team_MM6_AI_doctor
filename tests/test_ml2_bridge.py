"""Backend bridge regression; no source or model substitutions."""

import json
import os
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import AISettings
from app.models import Application, ApplicationDiagnosis, ApplicationItem
from app.schemas.analysis import PairCheck
from app.services import analysis
from app.services.ai_adapter import AIAdapter, AIInput
from app.services.ai_worker import execute
from app.services.excel_parser import parse_excel
from app.services.report import build_report
from scripts.rag_check import test_file as make_test_file
from tests.test_ai_analysis import MemorySession

AI_SOURCE = Path("ai/src").resolve()
pytestmark = [
    pytest.mark.skipif(not AI_SOURCE.is_dir(), reason="AI library is absent"),
    pytest.mark.usefixtures("no_network"),
]


@pytest.fixture(autouse=True)
def library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(AI_SOURCE))


def test_missing_registry_returns_clear_review(tmp_path: Path) -> None:
    settings = AISettings(mode="offline", registry_path=str(tmp_path / "missing.json"))
    adapter = AIAdapter(settings)
    check = adapter.analyze(AIInput(drug_name="Препарат", diagnosis="J45.9"), PairCheck())
    assert check.needs_review and check.category is None
    assert "ГРЛС не найден" in check.comment
    assert adapter.last_diagnostics["data_error"] == "REGISTRY_NOT_FOUND"


def test_corrupt_registry_is_safe_review(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("invalid", encoding="utf-8")
    adapter = AIAdapter(AISettings(mode="offline", registry_path=str(path)))
    check = adapter.analyze(AIInput(drug_name="Препарат", diagnosis="J45.9"), PairCheck())
    assert check.needs_review and "неверный формат" in check.comment
    assert adapter.last_diagnostics["data_error"] == "REGISTRY_INVALID"


def test_pair_contract_keeps_missing_purchase_fields_and_real_provenance() -> None:
    from llm_rag.ml2.pipeline import ML2Pipeline
    from llm_rag.ml2.registry import RegistrySnapshot

    from app.services.ml2_analysis import load_pair_contracts

    PairOriginal, PairItem, PairRequest, _ = load_pair_contracts()
    request = PairRequest(
        schema_version="1.0",
        request_id="pair",
        diagnosis="J45.9",
        context={},
        items=[
            PairItem(
                item_id="item",
                original=PairOriginal(
                    trade_name="Неизвестный препарат",
                    dosage=None,
                    quantity=None,
                    amount=None,
                    currency=None,
                ),
                inn=None,
                category=None,
                classification_reason=None,
                dosage_check={"status": "NOT_CHECKED", "reason": None, "missing_data": []},
                source_refs=[],
                upstream_status="PARTIAL",
                missing_data=[],
            )
        ],
    )
    registry = RegistrySnapshot(version="empty", as_of=datetime.now(UTC).date(), registrations=())
    batch = ML2Pipeline(registry).run(request)
    assert batch.request.is_demo is False
    assert batch.request.items[0].original.model_dump() == request.items[0].original.model_dump()
    assert batch.evaluations[0].classification.status == "INN_UNRESOLVED"


@pytest.fixture()
def real_paths() -> tuple[Path, Path]:
    root = os.environ.get("RAG_TEST_DATA_DIR")
    if not root:
        pytest.skip("Set RAG_TEST_DATA_DIR for verified real-source integration")
    return Path(root) / "grls/registry.json", Path(root) / "clinicalrecs"


def test_real_reference_to_worker_without_purchase_fields(real_paths: tuple[Path, Path]) -> None:
    registry, cache = real_paths
    adapter = AIAdapter(
        AISettings(
            mode="offline",
            registry_path=str(registry),
            clinical_cache_dir=str(cache),
            live_sources=True,
            pair_timeout_seconds=120,
        )
    )
    check = adapter.analyze(
        AIInput(drug_name="Сальбутамол", diagnosis="J45.9 Бронхиальная астма", age_group="adult"),
        PairCheck(needs_review=True),
    )
    assert adapter.last_mnn == "Сальбутамол"
    assert adapter.last_diagnostics["candidate_count"] == 1
    assert adapter.last_diagnostics["fragment_count"] == 3
    assert adapter.last_diagnostics["ml2_status"] == "NEEDS_REVIEW"
    assert adapter.last_diagnostics["model_calls"] == 0
    assert check.category is None and check.needs_review
    assert "359_3" in check.reference_version


def test_live_sources_flag_controls_clinical_lookup(real_paths: tuple[Path, Path]) -> None:
    registry, cache = real_paths
    result = execute(
        {
            "settings": AISettings(
                mode="offline",
                registry_path=str(registry),
                clinical_cache_dir=str(cache),
                live_sources=False,
            ).model_dump(),
            "pair": AIInput(drug_name="Сальбутамол", diagnosis="J45.9").model_dump(),
            "local": PairCheck().model_dump(mode="json"),
            "timeout": 20,
        }
    )
    assert result.mnn == "Сальбутамол"
    assert result.diagnostics["fragment_count"] == 0
    assert result.diagnostics["candidate_count"] == 0
    assert "Поиск клинических рекомендаций отключён" in result.check["comment"]
    assert result.diagnostics["live_sources"] is False


def test_real_xlsx_analysis_report_with_memory_storage(
    real_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpyxl import load_workbook

    from app.api.applications import _card

    registry, cache = real_paths
    parsed = parse_excel(make_test_file())
    assert len(parsed.items) == 1
    application = Application(
        id=uuid4(),
        filename="test-request.xlsx",
        age_group="adult",
        status="parsed",
        created_at=datetime.now(UTC),
        requires_review=False,
        diagnoses=[ApplicationDiagnosis(id=uuid4(), name="J45.9 Бронхиальная астма")],
        items=[ApplicationItem(id=uuid4(), **item.model_dump()) for item in parsed.items],
    )
    session = MemorySession(application)
    session.scalar = lambda _: None
    adapter = AIAdapter(
        AISettings(
            mode="offline",
            registry_path=str(registry),
            clinical_cache_dir=str(cache),
            live_sources=True,
            pair_timeout_seconds=120,
        )
    )
    monkeypatch.setattr(analysis, "AIAdapter", lambda: adapter)
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed"
    assert result.items[0].mnn == "Сальбутамол"
    assert result.traffic_light == "undetermined" and result.requires_review
    assert len(session.checks) == 1
    card = _card(result, session)
    assert card.application.age_group == "adult"
    assert card.application.reference_is_synthetic is False
    assert "359_3" in card.items[0].checks[0].reference_version
    report = load_workbook(BytesIO(build_report(card)))
    assert report["Препараты"].max_row == 2
    assert dict(report["Сводка"].values)["Возрастная группа"] == "Взрослые"
    assert json.loads(card.model_dump_json())["items"][0]["mnn"] == "Сальбутамол"
