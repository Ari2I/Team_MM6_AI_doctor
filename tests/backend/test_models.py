"""PostgreSQL smoke-тест актуальной схемы моделей."""

import os
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Application,
    ApplicationDiagnosis,
    ApplicationItem,
    ApplicationStatus,
    DrugCategory,
    ItemCheck,
    ValidationIssue,
)
from app.schemas.validation import IssueLevel


@pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1",
    reason="Для PostgreSQL smoke-теста задайте RUN_POSTGRES_TESTS=1 и примените миграции.",
)
def test_application_roundtrip_and_database_cascade() -> None:
    """Проверить чтение, четыре pair-checks, unique и каскадное удаление."""
    from app.db.session import engine

    assert engine.dialect.name == "postgresql"
    with engine.connect() as connection, connection.begin() as transaction:
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                first_diagnosis = ApplicationDiagnosis(name="Учебный диагноз 01")
                second_diagnosis = ApplicationDiagnosis(name="Учебный диагноз 02")
                first_item = ApplicationItem(
                    row_number=2,
                    drug_name="Учебный препарат 01",
                    dosage="5 мг",
                    quantity=Decimal("1.5"),
                    amount=Decimal("1234.50"),
                )
                second_item = ApplicationItem(
                    row_number=3,
                    drug_name="Учебный препарат 02",
                    mnn="Учебное МНН",
                    match_confidence=0.75,
                    match_note="Учебное сопоставление",
                )
                third_item = ApplicationItem(row_number=4)
                application = Application(
                    filename="synthetic.xlsx",
                    diagnoses=[first_diagnosis, second_diagnosis],
                    items=[first_item, second_item, third_item],
                    requires_review=True,
                    warning_message="Учебное предупреждение",
                    category_shares={"A": 0.4, "B": 0.2, "V": 0.1, "undetermined": 0.3},
                    validation_issues=[
                        ValidationIssue(
                            level=IssueLevel.WARNING,
                            code="empty_dosage",
                            message="Не указана дозировка.",
                            row=4,
                            column="dosage",
                        )
                    ],
                )
                first_item.item_checks = [
                    ItemCheck(diagnosis=first_diagnosis, category=DrugCategory.A),
                    ItemCheck(diagnosis=second_diagnosis, category=DrugCategory.B),
                ]
                second_item.item_checks = [
                    ItemCheck(diagnosis=first_diagnosis, category=DrugCategory.V),
                    ItemCheck(diagnosis=second_diagnosis, needs_review=True),
                ]
                session.add(application)
                session.commit()
                application_id = application.id
                assert isinstance(application_id, UUID)

            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                loaded = session.get(Application, application_id)
                assert loaded is not None
                assert loaded.status is ApplicationStatus.UPLOADED
                assert loaded.requires_review is True
                assert loaded.category_shares == {
                    "A": 0.4,
                    "B": 0.2,
                    "V": 0.1,
                    "undetermined": 0.3,
                }
                assert len(loaded.diagnoses) == 2
                assert len(loaded.items) == 3
                assert len(loaded.validation_issues) == 1
                items = sorted(loaded.items, key=lambda item: item.row_number)
                assert items[0].quantity == Decimal("1.5")
                assert items[0].amount == Decimal("1234.50")
                assert items[0].mnn is None
                assert len(items[0].item_checks) == 2
                assert len(items[1].item_checks) == 2
                assert items[1].match_confidence == 0.75
                assert items[2].item_checks == []
                assert connection.scalar(select(func.count()).select_from(ItemCheck)) == 4

                session.add(ItemCheck(item_id=items[0].id, diagnosis_id=loaded.diagnoses[0].id))
                with pytest.raises(IntegrityError):
                    session.flush()
                session.rollback()

            connection.execute(delete(Application).where(Application.id == application_id))
            for model in (ApplicationDiagnosis, ApplicationItem, ItemCheck, ValidationIssue):
                if model is ItemCheck:
                    count = connection.scalar(select(func.count()).select_from(model))
                else:
                    count = connection.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.application_id == application_id)
                    )
                assert count == 0
        finally:
            transaction.rollback()
