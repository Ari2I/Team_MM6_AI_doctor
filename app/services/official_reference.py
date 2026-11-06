"""Версионированный поиск по независимо импортированным официальным таблицам."""

import csv
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from hashlib import sha256
from io import BytesIO, StringIO
import os
from pathlib import Path
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zipfile import ZipFile

from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from rapidfuzz import fuzz, process

from app.services.text_utils import normalize_text

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_HOSTS = ("minzdrav.gov.ru", "rosminzdrav.ru", "gosuslugi.ru", "eaeunion.org", "pravo.gov.ru")
ICD_LETTERS = str.maketrans("АВСЕНКМОРТХУ", "ABCEHKMOPTXY")
ICD = re.compile(r"[A-Z][0-9]{2}(?:\.[0-9]{1,4})?")
CODE_AT_START = re.compile(r"^([A-ZА-Я][0-9]{2}(?:\.[0-9]{1,4})?)(?=\s|:|$)", re.I)


def canonical_code(value: str) -> str:
    return value.strip().upper().translate(ICD_LETTERS)


def name_key(value: str) -> str:
    return normalize_text(value.replace("®", "").replace("™", ""))


def inn_key(value: str) -> str:
    return "+".join(sorted(name_key(part) for part in value.split("+")))


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(Record):
    title: Text
    url: Text
    version: Text
    location: Text
    retrieved_at: date
    quote: Text

    @field_validator("url")
    @classmethod
    def official_https(cls, value: str) -> str:
        url = urlsplit(value)
        host = url.hostname or ""
        if (url.scheme != "https" or url.username is not None
                or not any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOSTS)):
            raise ValueError("Ожидается HTTPS-ссылка на сайт официального источника.")
        return value

    def citation(self) -> dict:
        digest = sha256(self.model_dump_json().encode()).hexdigest()[:24]
        return self.model_dump(mode="json") | {
            "source_id": digest, "fragment_id": digest, "source_type": "PROTOCOL",
            "publisher": urlsplit(self.url).hostname, "origin": "official",
        }


class RegistryEntry(Record):
    registration: Text
    trade_name: Text
    mnn: Text | None
    form: str = ""
    source: Source


class EvidenceEntry(Record):
    code: Text
    diagnosis: Text
    mnn: Text
    kind: Literal["standard", "guideline", "indication"]
    conditions: Text
    source: Source
    registration: Text | None = None
    scope: Literal["exact", "group"] = "exact"

    @field_validator("code")
    @classmethod
    def icd_code(cls, value: str) -> str:
        value = canonical_code(value)
        if ICD.fullmatch(value) is None:
            raise ValueError("Укажите точный код МКБ-10.")
        return value

    @model_validator(mode="after")
    def indication_has_registration(self):
        if self.scope == "group" and len(self.code) != 3:
            raise ValueError("Для группы МКБ-10 укажите код без дробной части.")
        if self.kind == "indication" and self.registration is None:
            raise ValueError("Укажите РУ для зарегистрированного показания.")
        return self


class CompositionEntry(Record):
    registration: Text
    form: Text
    source: Source


class OfficialSnapshot(Record):
    version: Text
    as_of: date
    coverage: Literal["registry_export", "limited", "historical"] = "limited"
    registrations: Annotated[tuple[RegistryEntry, ...], Field(min_length=1)]
    evidence: tuple[EvidenceEntry, ...] = ()
    compositions: tuple[CompositionEntry, ...] = ()

    @model_validator(mode="after")
    def source_links(self):
        registrations = {name_key(row.registration) for row in self.registrations}
        referenced = {name_key(row.registration) for row in self.compositions}
        referenced.update(name_key(row.registration) for row in self.evidence if row.registration)
        if not referenced <= registrations:
            raise ValueError("Состав или показание ссылается на РУ вне снимка.")
        return self


@dataclass
class DrugResolution:
    mnn: str | None
    candidates: list[str]
    registrations: tuple[RegistryEntry, ...] = ()


class OfficialIndex:
    def __init__(self, snapshot: OfficialSnapshot):
        self.snapshot = snapshot
        self.names: dict[str, list[RegistryEntry]] = {}
        self.diagnoses: dict[str, set[str]] = {}
        self.pairs: dict[tuple[str, str], list[EvidenceEntry]] = {}
        self.compositions: dict[str, list[CompositionEntry]] = {}
        for row in snapshot.registrations:
            keys = {name_key(row.trade_name)}
            if row.mnn:
                keys.add(inn_key(row.mnn))
            for key in keys:
                self.names.setdefault(key, []).append(row)
        for row in snapshot.evidence:
            self.diagnoses.setdefault(name_key(row.diagnosis), set()).add(row.code)
            self.pairs.setdefault((row.code, inn_key(row.mnn)), []).append(row)
        self.codes = {code for code, _ in self.pairs}
        self.groups = {row.code for row in snapshot.evidence if row.scope == "group"}
        for row in snapshot.compositions:
            self.compositions.setdefault(name_key(row.registration), []).append(row)
        self.labels = tuple(self.names)

    @lru_cache(maxsize=4096)
    def resolve(self, name: str) -> DrugResolution:
        key = name_key(name)
        rows = self.names.get(key) or self.names.get(inn_key(name), [])
        if rows:
            mnns = {inn_key(row.mnn): row.mnn for row in rows if row.mnn}
            if len(mnns) == 1 and all(row.mnn for row in rows):
                return DrugResolution(rows[0].mnn, [], tuple(rows))
            return DrugResolution(None, sorted(mnns.values()), tuple(rows))
        if not 4 <= len(key) <= 200:
            return DrugResolution(None, [])
        suggestions = process.extract(key, self.labels, scorer=fuzz.ratio, score_cutoff=86, limit=3)
        candidates = {row.mnn for label, _, _ in suggestions for row in self.names[label] if row.mnn}
        return DrugResolution(None, sorted(candidates)[:10])

    def details(self, drug: DrugResolution) -> dict:
        rows = drug.registrations
        regs = list(dict.fromkeys(name_key(row.registration) for row in rows))
        compositions = [row for reg in regs for row in self.compositions.get(reg, ())]
        return {
            "registration_count": len(regs),
            "registrations": [
                {"registration": row.registration, "trade_name": row.trade_name,
                 "mnn": row.mnn, "form": row.form, "source": row.source.citation()}
                for row in rows[:8]
            ],
            "compositions": [
                {"registration": row.registration, "form": row.form, "source": row.source.citation()}
                for row in compositions[:8]
            ],
        }

    def lookup(self, drug: DrugResolution, diagnosis: str) -> dict:
        def result(reason: str, text: str, rows: tuple | list = ()) -> dict:
            sources = {row.source.model_dump_json(): row.source.citation() for row in rows}
            conditions = list(dict.fromkeys(row.conditions for row in rows))
            return {
                "evidence_status": "UNCONFIRMED" if rows else "INSUFFICIENT_DATA",
                "reason_code": reason, "explanation": text, "effective_mode": "offline",
                "retrieval": "lexical", "review_sources": list(sources.values())[:15],
                "evidence_conditions": conditions[:15], "relationship_found": bool(rows),
                "match_candidates": drug.candidates,
            }

        if drug.mnn is None:
            if drug.candidates:
                return result("OFFICIAL_DRUG_UNRESOLVED", "Уточните препарат или МНН по предложенным вариантам.")
            if drug.registrations:
                return result("OFFICIAL_MNN_NOT_AVAILABLE", "Найдена регистрационная запись без указанного МНН.")
            return result("OFFICIAL_DRUG_NOT_FOUND", "Препарат отсутствует в загруженном снимке реестра.")
        value = diagnosis.strip()
        prefix = CODE_AT_START.match(value)
        if prefix:
            given = canonical_code(prefix[1])
            name = value[prefix.end():].lstrip(" :—–-")
            named_codes = self.diagnoses.get(name_key(name), set())
            compatible = {given}
            if "." in given:
                compatible.add(given.split(".")[0])
            if named_codes and named_codes.isdisjoint(compatible):
                return result("OFFICIAL_DIAGNOSIS_CONFLICT",
                              "Код МКБ-10 и указанное название диагноза расходятся.")
            codes = {given}
        else:
            codes = self.diagnoses.get(name_key(value), set())
        if len(codes) > 1:
            return result("OFFICIAL_DIAGNOSIS_UNRESOLVED", "Название соответствует нескольким кодам МКБ-10.")
        code = next(iter(codes), None)
        parent = code.split(".")[0] if code and "." in code else None
        if code not in self.codes and parent not in self.groups:
            return result("OFFICIAL_DIAGNOSIS_NOT_INDEXED", "Диагноз не найден в загруженных документах. Проверьте код МКБ-10.")
        regs = {name_key(row.registration) for row in drug.registrations}
        rows = [row for row in self.pairs.get((code, inn_key(drug.mnn)), ())
                if row.registration is None or name_key(row.registration) in regs]
        if not rows and parent in self.groups:
            rows = [row for row in self.pairs.get((parent, inn_key(drug.mnn)), ())
                    if row.scope == "group" and (row.registration is None or name_key(row.registration) in regs)]
            if rows:
                return result("OFFICIAL_GROUP_LINK_REQUIRES_REVIEW",
                              f"Найден источник для группы МКБ-10 {parent}. Уточните применимость к {code}.", rows)
        if not rows:
            return result("OFFICIAL_LINK_NOT_FOUND", "В загруженных документах связь для этой пары не найдена.")
        return result(
            "OFFICIAL_LINK_REQUIRES_REVIEW",
            "Найдена запись в официальном источнике. Специалисту нужно проверить условия применения.",
            rows,
        )


@lru_cache(maxsize=3)
def _load_cached(path: Path, stamp: tuple[int, int, int]) -> OfficialIndex:
    return OfficialIndex(OfficialSnapshot.model_validate_json(path.read_bytes()))


def load_official(path: Path) -> OfficialIndex:
    path = path.resolve()
    info = path.stat()
    return _load_cached(path, (info.st_mtime_ns, info.st_size, info.st_ino))


def official_path() -> Path | None:
    configured = os.getenv("OFFICIAL_REFERENCE_PATH")
    if configured:
        return Path(configured)
    local = ROOT / ".local" / "official-reference.json"
    return local if local.is_file() else None


COLUMNS = {
    "registration": {"regnumber", "номер регистрационного удостоверения", "номер ру"},
    "trade_name": {"tradename", "торговое наименование", "торговое наименование лекарственного препарата"},
    "mnn": {"internationalname", "мнн", "международное непатентованное или химическое наименование",
            "международное непатентованное наименование", "международное непатентованное наименование (мнн)"},
    "form": {"formrelease", "формы выпуска", "форма выпуска", "лекарственная форма"},
    "status": {"статус", "статус ру", "статус регистрационного удостоверения"},
    "end": {"enddate", "дата окончания действия регистрационного удостоверения"},
    "cancel": {"cancellationdate", "дата аннулирования регистрационного удостоверения",
               "дата аннулирования регистрацион- ного удостоверения"},
    "start": {"regdate", "дата регистрации"},
}
ACTIVE = ("действующий", "действует", "выдано по правилам еаэс")


def _active_export(name: str) -> bool:
    label = name_key(Path(name).stem.replace("_", " ")).rsplit("-", 1)[-1]
    return any(label == status or label.startswith(status + " ") for status in ACTIVE)


def _date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    if normalize_text(text) in {"", "-", "—", "бессрочно", "не ограничен", "н/д"}:
        return None
    raise ValueError(f"Не распознана дата в выгрузке ГРЛС: {text!r}")


def _registry_rows(rows, *, as_of, registry_url, document, assume_active):
    columns = {}
    for number, row in enumerate(rows, start=1):
        columns = {key: index for index, value in enumerate(row)
                   for key, aliases in COLUMNS.items() if name_key(str(value or "")) in aliases}
        if {"registration", "trade_name", "mnn"} <= columns.keys():
            break
        if number >= 30:
            return None
    else:
        return None
    if not assume_active and "status" not in columns and not {"start", "end", "cancel"} <= columns.keys():
        raise ValueError("Нужен статус РУ либо даты регистрации, окончания и аннулирования.")

    result = []
    for row_number, row in enumerate(rows, start=number + 1):
        def cell(key):
            index = columns.get(key, len(row))
            return row[index] if index < len(row) else None

        registration, trade, mnn, form = [str(cell(key) or "").strip()
                                          for key in ("registration", "trade_name", "mnn", "form")]
        if not registration or not trade:
            continue
        if name_key(mnn) in {"", "-", "—", "~", "н/д", "не указано"}:
            mnn = None
        status = name_key(str(cell("status") or ""))
        if status and not any(status == s or status.startswith(s + " ") or status.startswith(s + ",") for s in ACTIVE):
            continue
        if "status" in columns and not status and not assume_active:
            continue
        start, end, cancelled = (_date(cell(key)) for key in ("start", "end", "cancel"))
        if (start and start > as_of) or (end and end < as_of) or (cancelled and cancelled <= as_of):
            continue
        result.append(RegistryEntry(
            registration=registration, trade_name=trade, mnn=mnn, form=form,
            source=Source(title="Государственный реестр лекарственных средств", url=registry_url,
                          version=str(as_of), location=f"{document}, строка {row_number}",
                          retrieved_at=date.today(), quote=mnn or trade),
        ))
    return result


def import_grls(path: Path, *, as_of: date, registry_url: str, assume_active: bool = False):
    """Прочитать таблицы XLSX/CSV либо ZIP с официальными XLSX; состав загружается отдельно."""
    def workbook(data, document):
        book = load_workbook(data, read_only=True, data_only=True)
        try:
            active = assume_active or _active_export(document)
            for sheet in book:
                yield _registry_rows(
                    iter(sheet.values), as_of=as_of, registry_url=registry_url,
                    document=f"{document} / {sheet.title}", assume_active=active,
                )
        finally:
            book.close()

    groups = []
    match path.suffix.lower():
        case ".xlsx":
            groups.extend(workbook(path, path.name))
        case ".zip":
            with ZipFile(path) as archive:
                for member in archive.infolist():
                    name = Path(member.filename).name
                    if name.lower().endswith(".xlsx") and _active_export(name):
                        groups.extend(workbook(BytesIO(archive.read(member)), name))
        case ".csv":
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("cp1251")
            dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
            groups.append(_registry_rows(
                iter(csv.reader(StringIO(text), dialect)), as_of=as_of, registry_url=registry_url,
                document=path.name, assume_active=assume_active,
            ))
        case _:
            raise ValueError("Поддерживаются выгрузки ГРЛС ZIP, XLSX и CSV.")
    unique = {}
    for group in groups:
        for row in group or ():
            key = (name_key(row.registration), name_key(row.trade_name), inn_key(row.mnn or ""), name_key(row.form))
            unique.setdefault(key, row)
    if not unique:
        raise ValueError("В выгрузке не найдено пригодных записей ГРЛС.")
    return list(unique.values())
