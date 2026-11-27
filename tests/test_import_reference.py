"""Импорт справочника на реальном PostgreSQL с откатом тестовых данных."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ReferenceVersion
from app.scripts.import_reference import ReferenceDocument, import_reference, main, read_reference
from app.services.text_utils import normalize_text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Нужен PostgreSQL и RUN_POSTGRES_TESTS=1"
)
DEMO = Path(__file__).resolve().parents[1] / "data/reference/demo.json"


@pytest.fixture
def session() -> Iterator[Session]:
    from app.db.session import engine

    with engine.connect() as connection, connection.begin() as transaction:
        with Session(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as db_session:
            yield db_session
        transaction.rollback()


def document() -> ReferenceDocument:
    data = json.loads(DEMO.read_text(encoding="utf-8"))
    data["reference_version"] = f"import-test-{uuid4()}"
    return ReferenceDocument.model_validate(data)


def test_success_and_normalization(session: Session) -> None:
    source = document()
    source.drugs[0].trade_name = "  ЁЖ\u00a0  ТЕСТ  "
    source.drugs[0].mnn = "  Демо-МНН-01  "
    source.diagnoses[0].name = "  ЁЛКА\t  ТЕСТ "
    version = import_reference(session, source)
    session.expire_all()
    saved = session.get(ReferenceVersion, version.id)
    assert saved is not None
    assert saved.is_synthetic and not saved.is_active
    assert "Демонстрационные" in saved.name and "не клинические рекомендации" in saved.name
    assert "не клинические рекомендации" in saved.description.lower()
    assert len(saved.drugs) == 6 and len(saved.diagnoses) == 3 and len(saved.rules) == 6
    for row in saved.drugs:
        assert row.normalized_trade_name == normalize_text(row.trade_name)
        assert row.normalized_mnn == normalize_text(row.mnn)
    for row in saved.diagnoses:
        assert row.normalized_name == normalize_text(row.name)
        assert row.normalized_diagnosis_id == normalize_text(row.diagnosis_id)
    for row in saved.rules:
        assert row.normalized_mnn == normalize_text(row.mnn)
        assert row.normalized_diagnosis_id == normalize_text(row.diagnosis_id)


def test_bad_reference_and_json(session: Session, tmp_path: Path) -> None:
    before = session.scalar(select(func.count()).select_from(ReferenceVersion))
    data = json.loads(DEMO.read_text(encoding="utf-8"))
    data["rules"][0]["diagnosis_id"] = "missing"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="несуществующие диагнозы"):
        read_reference(path)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="Некорректный JSON"):
        read_reference(path)
    assert session.scalar(select(func.count()).select_from(ReferenceVersion)) == before
    assert main([str(path)]) == 1


def test_repeat_is_rejected(session: Session) -> None:
    source = document()
    first = import_reference(session, source)
    with pytest.raises(ValueError, match="уже импортирована"):
        import_reference(session, source, activate=True)
    assert (
        session.scalar(
            select(func.count())
            .select_from(ReferenceVersion)
            .where(ReferenceVersion.version == first.version)
        )
        == 1
    )
    assert not session.get(ReferenceVersion, first.id).is_active


def test_activation_and_rollback(session: Session) -> None:
    first = import_reference(session, document(), activate=True)
    second_source = document()
    second = import_reference(session, second_source, activate=True)
    session.refresh(first)
    assert not first.is_active and second.is_active
    assert (
        session.scalar(
            select(func.count()).select_from(ReferenceVersion).where(ReferenceVersion.is_active)
        )
        == 1
    )
    # A failed insertion after deactivation must restore the active version.
    bad_source = document()
    bad_source.drugs[0].trade_name = None
    with pytest.raises(ValueError, match="целостность"):
        import_reference(session, bad_source, activate=True)
    session.refresh(second)
    assert second.is_active
    assert (
        session.scalar(
            select(func.count())
            .select_from(ReferenceVersion)
            .where(ReferenceVersion.version == bad_source.reference_version)
        )
        == 0
    )
