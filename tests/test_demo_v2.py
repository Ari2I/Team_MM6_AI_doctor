"""Синтетические общие данные: согласованность corpus и полный путь через PostgreSQL."""

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.api.applications import get_session
from app.main import app
from app.scripts.import_reference import import_reference, read_reference
from app.services.analysis import _load_snapshot
from app.services.matching import match_diagnosis, match_drug
from app.services.rules import check_pair

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "data/reference/demo-v2.json"
CASES = [
    ("correct.xlsx", "green", 0, set(), 3),
    ("typo.xlsx", "yellow", 1, {"DRUG_AMBIGUOUS"}, 3),
    ("unknown.xlsx", "undetermined", 1, {"DRUG_NOT_FOUND"}, 2),
    ("two_diagnoses.xlsx", "yellow", 2, {"NO_RULE_FOR_PAIR"}, 4),
]


def test_rules_match_only_unconditional_corpus_assertions() -> None:
    document = read_reference(REFERENCE)
    corpus = json.loads((ROOT / "ai/examples/corpus.json").read_text(encoding="utf-8"))
    categories = {"А": "A", "Б": "B", "В": "V"}
    expected = {
        (fragment["inn"], fragment["diagnosis"], categories[assertion["category"]])
        for fragment in corpus["fragments"]
        if not fragment["required_context"]
        for assertion in fragment["assertions"]
        if assertion["kind"] == "CATEGORY"
    }
    diagnoses = {row.diagnosis_id: row.name for row in document.diagnoses}
    actual = {(row.mnn, diagnoses[row.diagnosis_id], row.category.value) for row in document.rules}
    assert actual == expected
    assert document.reference_version == "demo-api-2" and document.is_synthetic
    assert "Синтетические" in document.name and "не клинические" in document.name
    assert "Синтетические" in document.description and "не клинические" in document.description
    assert {row.mnn for row in document.drugs} == {f["inn"] for f in corpus["fragments"]}
    assert all(row.source and corpus["corpus_version"] in row.source for row in document.rules)


@pytest.mark.parametrize("filename", [case[0] for case in CASES])
def test_demo_workbooks_have_complete_values(filename: str) -> None:
    with (ROOT / "tests/data/sample_ok.xlsx").open("rb") as source:
        sample = load_workbook(source, read_only=True)
        headers = next(sample.active.values)
        sample.close()
    workbook = load_workbook(ROOT / "data/demo" / filename, read_only=True)
    try:
        assert workbook.sheetnames == ["Заявка"]
        rows = list(workbook.active.values)
        assert rows[0] == headers
        for name, dosage, quantity, amount, _ in rows[1:]:
            assert name and dosage
            assert isinstance(quantity, int) and quantity > 0
            assert isinstance(amount, (int, float)) and amount > 0
    finally:
        workbook.close()


@pytest.fixture
def demo_client() -> Iterator[TestClient]:
    if os.environ.get("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Нужен PostgreSQL и RUN_POSTGRES_TESTS=1")
    from app.db.session import engine

    with engine.connect() as connection, connection.begin() as transaction:
        with Session(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as session:
            version = import_reference(session, read_reference(REFERENCE), activate=True)
            session.refresh(version)
            assert version.is_active and version.is_synthetic
            assert version.version == "demo-api-2"
            assert (len(version.drugs), len(version.diagnoses), len(version.rules)) == (4, 2, 3)
            snapshot = _load_snapshot(session)
            conditional = check_pair(
                match_drug("Симбикорт Турбухалер", snapshot),
                match_diagnosis("Демо-диагноз", snapshot),
                snapshot,
            )
            assert conditional.needs_review
            assert conditional.review_reason.value == "NO_RULE_FOR_PAIR"
            app.dependency_overrides[get_session] = lambda: session
            try:
                with TestClient(app) as client:
                    yield client
            finally:
                app.dependency_overrides.pop(get_session, None)
        transaction.rollback()


@pytest.mark.parametrize("filename,color,review_count,reasons,check_count", CASES)
def test_demo_analysis(
    demo_client: TestClient,
    filename: str,
    color: str,
    review_count: int,
    reasons: set[str],
    check_count: int,
) -> None:
    response = demo_client.post(
        "/applications",
        files={"file": (filename, (ROOT / "data/demo" / filename).read_bytes())},
    )
    assert response.status_code == 201
    card = response.json()
    application = card["application"]
    assert application["status"] == "analyzed"
    assert application["traffic_light"] == color
    assert application["requires_review"] == bool(review_count)
    assert application["category_shares"]["total_amount"] == 1000
    assert sum(any(c["needs_review"] for c in i["checks"]) for i in card["items"]) == review_count
    checks = [check for item in card["items"] for check in item["checks"]]
    assert len(checks) == check_count
    assert {c["review_reason"] for c in checks if c["needs_review"]} == reasons
    assert all(c["reference_version"] == "demo-api-2" for c in checks)
    assert all(c["source"] for c in checks if not c["needs_review"])
    assert all(c["comment"] for c in checks if c["needs_review"])
    assert card["disclaimer"]
    assert not {"missing_dosage", "missing_quantity", "missing_amount"} & {
        issue["code"] for issue in card["issues"]
    }
    if filename == "two_diagnoses.xlsx":
        assert len(card["diagnoses"]) == 2
        assert len(card["items"][0]["checks"]) == 2
        assert {c["category"] for c in card["items"][0]["checks"]} == {"A", None}
    saved = demo_client.get(f"/applications/{application['id']}")
    assert saved.json() == card
