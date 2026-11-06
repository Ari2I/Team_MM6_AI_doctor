"""Импорт синтетического справочника в PostgreSQL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import ReferenceDiagnosis, ReferenceDrug, ReferenceRule, ReferenceVersion
from app.models.enums import DrugCategory
from app.services.text_utils import normalize_text


class InputDrug(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    trade_name: Annotated[str, Field(min_length=1)]
    mnn: Annotated[str, Field(min_length=1)]


class InputDiagnosis(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    diagnosis_id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]


class InputRule(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    mnn: Annotated[str, Field(min_length=1)]
    diagnosis_id: Annotated[str, Field(min_length=1)]
    category: DrugCategory | None = None
    source: str | None = None


class ReferenceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reference_version: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)] = (
        "Демонстрационные данные; не клинические рекомендации"
    )
    description: Annotated[str, Field(min_length=1)] = (
        "Демонстрационные синтетические данные; не клинические рекомендации."
    )
    is_synthetic: Literal[True] = True
    drugs: list[InputDrug] = Field(default_factory=list)
    diagnoses: list[InputDiagnosis] = Field(default_factory=list)
    rules: list[InputRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> ReferenceDocument:
        diagnosis_ids = {normalize_text(item.diagnosis_id) for item in self.diagnoses}
        mnn_values = {normalize_text(item.mnn) for item in self.drugs}
        if len(diagnosis_ids) != len(self.diagnoses):
            raise ValueError("В справочнике повторяются идентификаторы диагнозов.")
        rule_pairs = [
            (normalize_text(item.mnn), normalize_text(item.diagnosis_id)) for item in self.rules
        ]
        if len(set(rule_pairs)) != len(rule_pairs):
            raise ValueError("В справочнике повторяются правила для одной пары МНН и диагноза.")
        missing_diagnoses = sorted(
            {
                item.diagnosis_id
                for item in self.rules
                if normalize_text(item.diagnosis_id) not in diagnosis_ids
            }
        )
        missing_mnns = sorted(
            {item.mnn for item in self.rules if normalize_text(item.mnn) not in mnn_values}
        )
        if missing_diagnoses:
            raise ValueError(
                f"Правила ссылаются на несуществующие диагнозы: {', '.join(missing_diagnoses)}."
            )
        if missing_mnns:
            raise ValueError(f"Правила ссылаются на несуществующие МНН: {', '.join(missing_mnns)}.")
        return self


def read_reference(path: Path) -> ReferenceDocument:
    try:
        return ReferenceDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise ValueError(
            "Не удалось прочитать файл справочника. Проверьте путь и кодировку UTF-8."
        ) from error
    except ValidationError as error:
        messages = []
        for item in error.errors():
            location = ".".join(str(part) for part in item["loc"]) or "файл"
            if item["type"] == "value_error":
                message = str(item.get("ctx", {}).get("error", "Некорректные данные"))
            elif item["type"] == "json_invalid":
                message = "Некорректный JSON"
            else:
                message = "Поле отсутствует или имеет недопустимое значение"
            messages.append(f"{location}: {message}")
        raise ValueError("Файл справочника не прошёл проверку: " + "; ".join(messages)) from error


def _write_reference(
    session: Session, document: ReferenceDocument, activate: bool
) -> ReferenceVersion:
    # Serialize imports/activation, including concurrent CLI processes.
    session.execute(text("LOCK TABLE reference_versions IN EXCLUSIVE MODE"))
    existing = session.scalar(
        select(ReferenceVersion).where(ReferenceVersion.version == document.reference_version)
    )
    if existing is not None:
        raise ValueError(f"Версия справочника «{document.reference_version}» уже импортирована.")
    if activate:
        session.execute(update(ReferenceVersion).values(is_active=False))
    version = ReferenceVersion(
        version=document.reference_version,
        name=(
            f"Демонстрационные данные: {document.name} ({document.reference_version}); "
            "не клинические рекомендации"
        ),
        description=f"Демонстрационные данные. {document.description} Не клинические рекомендации.",
        is_synthetic=True,
        is_active=activate,
    )
    version.drugs = [
        ReferenceDrug(
            trade_name=item.trade_name,
            normalized_trade_name=normalize_text(item.trade_name),
            mnn=item.mnn,
            normalized_mnn=normalize_text(item.mnn),
        )
        for item in document.drugs
    ]
    version.diagnoses = [
        ReferenceDiagnosis(
            diagnosis_id=item.diagnosis_id,
            normalized_diagnosis_id=normalize_text(item.diagnosis_id),
            name=item.name,
            normalized_name=normalize_text(item.name),
        )
        for item in document.diagnoses
    ]
    version.rules = [
        ReferenceRule(
            mnn=item.mnn,
            normalized_mnn=normalize_text(item.mnn),
            diagnosis_id=item.diagnosis_id,
            normalized_diagnosis_id=normalize_text(item.diagnosis_id),
            category=item.category,
            source=item.source,
        )
        for item in document.rules
    ]
    session.add(version)
    session.flush()
    return version


def import_reference(
    session: Session, document: ReferenceDocument, activate: bool = False
) -> ReferenceVersion:
    """Импортировать целиком и завершить транзакцию; при ошибке откатить все изменения."""
    try:
        version = _write_reference(session, document, activate)
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ValueError(
            "Версия справочника не импортирована: нарушена целостность данных."
        ) from error
    except Exception:
        session.rollback()
        raise
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт синтетического справочника.")
    parser.add_argument("path", type=Path, help="Путь к JSON-файлу справочника")
    parser.add_argument(
        "--activate", action="store_true", help="Сделать импортированную версию активной"
    )
    args = parser.parse_args(argv)
    try:
        document = read_reference(args.path)
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            version = import_reference(session, document, activate=args.activate)
            output = (
                f"Импорт завершён: версия {version.version}, "
                f"диагнозов {len(version.diagnoses)}, препаратов {len(version.drugs)}, "
                f"правил {len(version.rules)}, активна={version.is_active}."
            )
    except ValueError as error:
        print(f"Ошибка импорта: {error}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print(
            "Ошибка импорта: не удалось сохранить справочник. Проверьте доступность и миграции БД.",
            file=sys.stderr,
        )
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
