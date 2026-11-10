"""Локальный ГРЛС для ML-2, без моделей и импортов приложения demo."""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile

from pydantic import BaseModel, ConfigDict, Field, field_validator


def name_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value.replace("®", "").replace("™", ""))
    return " ".join(value.casefold().replace("ё", "е").split())


def inn_key(value: str) -> str:
    return "+".join(sorted(name_key(p).strip("[] ") for p in value.split("+")))


def canonical_code(value: str) -> str:
    return (
        value.strip().upper().translate(str.maketrans("АВСЕНКМОРТХУ", "ABCEHKMOPTXY"))
    )


def _strength(value: str) -> set[tuple[str, str]]:
    return set(
        re.findall(
            r"(?<![\d.,])([0-9]+(?:\.[0-9]+)?)\s*(мкг|мг|г|ме|%)(?!\w)",
            name_key(value).replace(",", "."),
        )
    )


class RegistrySource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    title: str = Field(min_length=1)
    url: str
    version: str = Field(min_length=1)
    location: str = Field(min_length=1)
    retrieved_at: date
    quote: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def official_url(cls, value: str) -> str:
        url = urlsplit(value)
        host = url.hostname or ""
        if (
            url.scheme != "https"
            or url.username
            or url.password
            or not any(
                host == h or host.endswith("." + h)
                for h in ("rosminzdrav.ru", "minzdrav.gov.ru", "eaeunion.org")
            )
        ):
            raise ValueError("Нужна HTTPS-ссылка на официальный реестр")
        return value

    def citation(self) -> dict:
        digest = sha256(self.model_dump_json().encode()).hexdigest()[:24]
        return self.model_dump(mode="json") | {
            "source_id": digest,
            "fragment_id": digest,
        }


class RegistryEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    registration: str = Field(min_length=1)
    trade_name: str = Field(min_length=1)
    mnn: str | None
    form: str = ""
    source: RegistrySource


class RegistrySnapshot(BaseModel):
    # Из старого JSON-снимка читается только реестр; клинические правила не импортируются.
    model_config = ConfigDict(frozen=True, extra="ignore")
    version: str = Field(min_length=1)
    as_of: date
    registrations: tuple[RegistryEntry, ...]

    @classmethod
    def load(cls, path: Path) -> RegistrySnapshot:
        return cls.model_validate_json(path.read_bytes())


@dataclass(frozen=True)
class DrugResolution:
    mnn: str | None
    candidates: list[str]
    registrations: tuple[RegistryEntry, ...] = ()


class RegistryIndex:
    def __init__(self, snapshot: RegistrySnapshot):
        self.snapshot = snapshot
        self.names: dict[str, list[RegistryEntry]] = {}
        for row in snapshot.registrations:
            for key in {name_key(row.trade_name), inn_key(row.mnn or "")} - {""}:
                self.names.setdefault(key, []).append(row)

    def resolve(self, name: str, dosage: str | None = None) -> DrugResolution:
        rows = self.names.get(name_key(name)) or self.names.get(inn_key(name), [])
        strength = _strength(dosage or "")
        matching = [row for row in rows if strength and strength <= _strength(row.form)]
        if matching:
            rows = matching
        candidates = {inn_key(row.mnn): row.mnn for row in rows if row.mnn}
        if len(candidates) == 1 and all(row.mnn for row in rows):
            return DrugResolution(next(iter(candidates.values())), [], tuple(rows))
        return DrugResolution(None, sorted(candidates.values()), tuple(rows))


_COLUMNS = {
    "registration": {"regnumber", "номер регистрационного удостоверения", "номер ру"},
    "trade_name": {
        "tradename",
        "торговое наименование",
        "торговое наименование лекарственного препарата",
    },
    "mnn": {
        "internationalname",
        "мнн",
        "международное непатентованное или химическое наименование",
        "международное непатентованное наименование",
        "международное непатентованное наименование (мнн)",
    },
    "form": {"formrelease", "формы выпуска", "форма выпуска", "лекарственная форма"},
    "status": {"статус", "статус ру", "статус регистрационного удостоверения"},
    "start": {"regdate", "дата регистрации"},
    "end": {"enddate", "дата окончания действия регистрационного удостоверения"},
    "cancel": {
        "cancellationdate",
        "дата аннулирования регистрационного удостоверения",
        "дата аннулирования регистрацион- ного удостоверения",
    },
}
_ACTIVE = {"действующий", "действует", "выдано по правилам еаэс"}


def _active_filename(name: str) -> bool:
    tail = name_key(Path(name).stem.replace("_", " ")).rsplit("-", 1)[-1]
    return any(tail == s or tail.startswith(s + " ") for s in _ACTIVE)


def _date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if name_key(text) in {"", "-", "—", "бессрочно", "не ограничен", "н/д"}:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError("Не распознана дата в выгрузке ГРЛС")


def _read_rows(rows, *, as_of: date, registry_url: str, document: str, active: bool):
    for number, row in enumerate(rows, 1):
        columns = {
            key: i
            for i, value in enumerate(row)
            for key, aliases in _COLUMNS.items()
            if name_key(str(value or "")) in aliases
        }
        if {"registration", "trade_name", "mnn"} <= columns.keys():
            break
        if number >= 30:
            return
    else:
        return
    if (
        not active
        and "status" not in columns
        and not {"start", "end", "cancel"} <= columns.keys()
    ):
        raise ValueError("В таблице ГРЛС нет статуса или дат действия РУ")
    header_number = number
    for row_number, row in enumerate(rows, header_number + 1):

        def cell(key, row=row):
            i = columns.get(key, len(row))
            return row[i] if i < len(row) else None

        reg, trade, mnn, form = (
            str(cell(k) or "").strip()
            for k in ("registration", "trade_name", "mnn", "form")
        )
        if not reg or not trade:
            continue
        status = name_key(str(cell("status") or ""))
        if status and not any(
            status == s or status.startswith((s + " ", s + ",")) for s in _ACTIVE
        ):
            continue
        if "status" in columns and not status and not active:
            continue
        start, end, cancelled = (_date(cell(k)) for k in ("start", "end", "cancel"))
        if (
            (start and start > as_of)
            or (end and end < as_of)
            or (cancelled and cancelled <= as_of)
        ):
            continue
        if name_key(mnn) in {"", "-", "—", "~", "н/д", "не указано"}:
            mnn = None
        yield RegistryEntry(
            registration=reg,
            trade_name=trade,
            mnn=mnn,
            form=form,
            source=RegistrySource(
                title="Государственный реестр лекарственных средств",
                url=registry_url,
                version=str(as_of),
                retrieved_at=date.today(),
                location=f"{document}, строка {row_number}",
                quote=mnn or trade,
            ),
        )


def build_snapshot(
    path: Path,
    as_of: date,
    *,
    registry_url: str,
    coverage: str = "registry_export",
    assume_active: bool = False,
) -> RegistrySnapshot:
    """Импорт датированной выгрузки. XLSX/ZIP требуют optional-зависимость openpyxl."""

    def workbook(data, name):
        from openpyxl import load_workbook

        book = load_workbook(data, read_only=True, data_only=True)
        try:
            for sheet in book:
                yield from _read_rows(
                    iter(sheet.values),
                    as_of=as_of,
                    registry_url=registry_url,
                    document=f"{name} / {sheet.title}",
                    active=assume_active or _active_filename(name),
                )
        finally:
            book.close()

    if coverage != "registry_export":
        raise ValueError("ML-2 импортирует только реестр, без клинических правил")
    rows = []
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            members = archive.infolist()
            if sum(m.file_size for m in members) > 500_000_000:
                raise ValueError("Распакованный архив ГРЛС слишком велик")
            for member in members:
                if member.filename.lower().endswith(".xlsx") and _active_filename(
                    member.filename
                ):
                    rows.extend(
                        workbook(
                            BytesIO(archive.read(member)), Path(member.filename).name
                        )
                    )
    elif path.suffix.lower() == ".xlsx":
        rows.extend(workbook(path, path.name))
    elif path.suffix.lower() == ".csv":
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1251")
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
        rows.extend(
            _read_rows(
                iter(csv.reader(StringIO(text), dialect)),
                as_of=as_of,
                registry_url=registry_url,
                document=path.name,
                active=assume_active,
            )
        )
    else:
        raise ValueError("Поддерживаются ГРЛС ZIP, XLSX и CSV")
    unique = {
        (
            name_key(r.registration),
            name_key(r.trade_name),
            inn_key(r.mnn or ""),
            r.form,
        ): r
        for r in rows
    }
    if not unique:
        raise ValueError("В выгрузке нет пригодных действующих записей ГРЛС")
    snapshot = RegistrySnapshot(
        version="pending", as_of=as_of, registrations=tuple(unique.values())
    )
    digest = sha256(snapshot.model_dump_json().encode()).hexdigest()[:16]
    return snapshot.model_copy(update={"version": f"ml2-grls-{as_of}-{digest}"})
