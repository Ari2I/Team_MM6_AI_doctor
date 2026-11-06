"""Валидация и атомарная активация демонстрационного справочника."""

import hashlib
from pathlib import Path

from pydantic import BaseModel, model_validator
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.models import reference as db
from app.schemas.analysis import ReferenceDrug, ReferenceSnapshot
from app.services.text_utils import normalize_text
from llm_rag.classification import classify_category
from llm_rag.corpus import Corpus

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_FILE = ROOT / "data" / "demo" / "reference.json"
DEMO_FILE = ROOT / "examples" / "demo.xlsx"
CATEGORIES = {"A": "А", "B": "Б", "V": "В"}


class CatalogDrug(ReferenceDrug):
    source_id: str
    composition_quote: str
    composition_location: str


class ReferenceBundle(ReferenceSnapshot):
    drugs: list[CatalogDrug]
    corpus: Corpus

    @model_validator(mode="after")
    def validate_links(self):
        if self.reference_version != self.corpus.corpus_version:
            raise ValueError("Версии справочника и корпуса различаются.")
        diagnoses = {normalize_text(row.diagnosis_id): row.name for row in self.diagnoses}
        if len(diagnoses) != len(self.diagnoses):
            raise ValueError("Повтор кода диагноза после нормализации.")
        sources = {source.source_id for source in self.corpus.sources}
        if any(drug.source_id not in sources or not drug.composition_quote.strip()
               or not drug.composition_location.strip() for drug in self.drugs):
            raise ValueError("Проверьте происхождение записей каталога препаратов.")
        mnns = {row.normalized_mnn for row in self.drugs}
        fragments = {row.fragment_id: row for row in self.corpus.fragments}
        seen = set()
        for rule in self.rules:
            key = (rule.normalized_mnn, rule.normalized_diagnosis_id)
            if key in seen:
                raise ValueError("Повтор правила после нормализации.")
            seen.add(key)
            name = diagnoses.get(rule.normalized_diagnosis_id)
            fragment = fragments.get(rule.source)
            if not name or rule.normalized_mnn not in mnns or not fragment or rule.category is None:
                raise ValueError("Неразрешимая ссылка правила.")
            category = CATEGORIES[rule.category]
            classified = classify_category(rule.mnn, name, self.corpus)
            if (
                fragment.required_context
                or normalize_text(fragment.inn) != rule.normalized_mnn
                or normalize_text(fragment.diagnosis) != normalize_text(name)
                or classified.category != category
                or not any(a.kind == "CATEGORY" and a.category == category
                           and a.upstream_reason == classified.reason for a in fragment.assertions)
            ):
                raise ValueError("Фрагмент не подтверждает правило справочника.")
        return self

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def load_bundle() -> ReferenceBundle:
    return ReferenceBundle.model_validate_json(REFERENCE_FILE.read_bytes())


def seed_reference(session: Session, bundle: ReferenceBundle) -> None:
    """Сериализовать активацию и защитить прежние заявки от подмены версии."""
    bundle = ReferenceBundle.model_validate(bundle.model_dump())
    session.execute(text("SELECT pg_advisory_xact_lock(73170611)"))
    version = session.scalar(
        select(db.ReferenceVersion).where(db.ReferenceVersion.version == bundle.reference_version)
    )
    if version is None:
        version = db.ReferenceVersion(version=bundle.reference_version, fingerprint=bundle.content_digest)
        version.drugs = [db.ReferenceDrug(trade_name=row.trade_name, mnn=row.mnn)
                         for row in bundle.drugs]
        version.diagnoses = [db.ReferenceDiagnosis(diagnosis_id=row.diagnosis_id, name=row.name)
                             for row in bundle.diagnoses]
        version.rules = [db.ReferenceRule(mnn=row.mnn, diagnosis_id=row.diagnosis_id,
                                         category=row.category, source=row.source)
                         for row in bundle.rules]
        session.add(version)
        session.flush()
    elif version.fingerprint != bundle.content_digest:
        raise ValueError("Содержимое версии изменилось. Укажите новую версию набора.")
    session.execute(
        update(db.ReferenceVersion)
        .where(db.ReferenceVersion.id != version.id, db.ReferenceVersion.is_active.is_(True))
        .values(is_active=False)
    )
    version.is_active = True
    session.flush()


if __name__ == "__main__":
    from app.db.session import SessionLocal

    bundle = load_bundle()
    with SessionLocal.begin() as session:
        seed_reference(session, bundle)
    print(f"Активирован демонстрационный набор {bundle.reference_version}")
