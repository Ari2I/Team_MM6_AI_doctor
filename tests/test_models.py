"""Smoke-тест на PostgreSQL; запускать после alembic upgrade head в Compose."""

import os
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    ApplicationDiagnosis,
    ApplicationItem,
    ApplicationStatus,
    DrugCategory,
    TrafficLight,
    ValidationIssue,
)
from app.schemas.validation import IssueLevel


@pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1",
    reason="Для PostgreSQL smoke-теста задайте RUN_POSTGRES_TESTS=1 и примените миграции.",
)
def test_application_roundtrip_and_database_cascade() -> None:
    """Сохранить, прочитать и удалить заявку; изменения теста откатываются."""
    from app.db.session import engine

    assert engine.dialect.name == "postgresql"
    with engine.connect() as connection, connection.begin() as transaction:
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                first_diagnosis = ApplicationDiagnosis(name="Учебный диагноз 01")
                second_diagnosis = ApplicationDiagnosis(name="Учебный диагноз 02")
                application = Application(
                    filename="synthetic.xlsx",
                    diagnoses=[first_diagnosis, second_diagnosis],
                    items=[
                        ApplicationItem(
                            row_number=2,
                            drug_name="Учебный препарат 01",
                            dosage="5 мг",
                            quantity=Decimal("1.5"),
                            amount=Decimal("1234.50"),
                            diagnosis=first_diagnosis,
                        ),
                        ApplicationItem(
                            row_number=3,
                            drug_name="Учебный препарат 02",
                            diagnosis=second_diagnosis,
                            mnn="Учебное МНН",
                            category=DrugCategory.V,
                            confidence=0.75,
                            source="synthetic-rule",
                            reference_version="test-v1",
                            comment="Учебный комментарий",
                            needs_review=True,
                            review_reason="Учебная причина",
                        ),
                        ApplicationItem(row_number=4),  # Диагноз у строки необязателен.
                    ],
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
                session.add(application)
                session.commit()
                application_id = application.id
                assert isinstance(application_id, UUID)

            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                loaded = session.get(Application, application_id)
                assert loaded is not None
                assert loaded.status is ApplicationStatus.UPLOADED
                assert loaded.traffic_light is loaded.summary is loaded.secretary_decision is None
                assert loaded.created_at.tzinfo is not None
                assert loaded.updated_at.tzinfo is not None
                assert len(loaded.diagnoses) == 2
                assert len(loaded.items) == 3
                assert len(loaded.validation_issues) == 1
                assert loaded.validation_issues[0].level is IssueLevel.WARNING
                items = sorted(loaded.items, key=lambda item: item.row_number)
                assert items[0].quantity == Decimal("1.5")
                assert items[0].amount == Decimal("1234.50")
                assert items[0].diagnosis in loaded.diagnoses
                assert items[0].category is items[0].mnn is None
                assert items[0].needs_review is False
                assert items[1].category is DrugCategory.V
                assert items[1].needs_review is True
                assert items[2].diagnosis_id is None
                loaded.traffic_light = TrafficLight.UNDETERMINED
                loaded.summary = "Учебная рекомендация"
                loaded.secretary_decision = "Учебное решение человека"
                session.commit()

            # SQL DELETE проверяет каскад PostgreSQL без помощи ORM relationships.
            connection.execute(delete(Application).where(Application.id == application_id))
            for model in (ApplicationDiagnosis, ApplicationItem, ValidationIssue):
                count = connection.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.application_id == application_id)
                )
                assert count == 0
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(Application)
                    .where(Application.id == application_id)
                )
                == 0
            )
        finally:
            transaction.rollback()
