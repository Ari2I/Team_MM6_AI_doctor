"""Импорт синтетического справочника общим импортёром."""

from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import ReferenceVersion
from app.scripts.import_reference import import_reference, read_reference


def import_demo(session: Session) -> ReferenceVersion:
    document = read_reference(Path(__file__).resolve().parents[1] / "data/reference/demo.json")
    version = session.scalar(
        select(ReferenceVersion).where(ReferenceVersion.version == document.reference_version)
    )
    if version is None:
        return import_reference(session, document, activate=True)
    session.execute(update(ReferenceVersion).values(is_active=False))
    session.flush()
    version.is_active = True
    session.commit()
    return version
