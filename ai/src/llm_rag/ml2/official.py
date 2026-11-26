"""Каталог и полные тексты действующих КР из API Минздрава."""

from datetime import UTC, datetime
from functools import cached_property
from html.parser import HTMLParser
import json
import re

import httpx

from llm_rag.integration_budget import source_timeout
from .clinicalrecs import CatalogCandidate, CachedRecommendation, ClinicalRecommendation, TreatmentSegment

API = "https://apicr.minzdrav.gov.ru/API.ashx"
POPULATIONS = {1: "Взрослые", 2: "Дети", 3: "Взрослые, дети"}


def _get(operation, *, deadline, **params):
    with httpx.stream("GET", API, params={"op": operation, **params}, timeout=source_timeout(deadline, 45)) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes():
            source_timeout(deadline, 45)
            content.extend(chunk)
            if len(content) > 64_000_000:
                raise ValueError("Превышен размер документа КР.")
    return json.loads(content)


class _Paragraphs(HTMLParser):
    """Сохранять текст и границы абзацев, игнорируя оформление Word."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.paragraphs = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "br", "tr", "li", "div"}:
            self.flush()

    def handle_endtag(self, tag):
        if tag in {"p", "tr", "li", "div"}:
            self.flush()
        elif tag == "td":
            self.parts.append(" ")

    def flush(self):
        text = " ".join("".join(self.parts).split())
        if text:
            self.paragraphs.append(text)
        self.parts.clear()


class ClinicalSources:
    """Один каталог и одна загрузка каждой редакции в пределах заявки."""

    def __init__(self, deadline=None):
        self.deadline = deadline
        self.editions = {}

    @cached_property
    def catalog(self):
        rows = _get("GetJsonClinrecs", deadline=self.deadline)
        if not isinstance(rows, list) or not rows:
            raise ValueError("Не удалось проверить каталог КР.")
        return rows

    def candidates(self, code, age_group):
        for row in self.catalog:
            if row["status"] != 0 or row["apply_status"] != "Применяется":
                continue
            codes = row["Mkb"] or []
            if not {code, code.split(".")[0]}.intersection(codes):
                continue
            population = POPULATIONS.get(row["age"])
            edition = row["id"]
            if not population or not re.fullmatch(r"[0-9]+_[0-9]+", edition):
                raise ValueError("Некорректный паспорт редакции КР.")
            if age_group and age_group.casefold() not in population.casefold():
                continue
            url = "https://cr.minzdrav.gov.ru/preview-cr/" + edition
            yield CatalogCandidate(
                row["name"], edition, row["publish_date"], population, tuple(codes), url, url
            )

    def load(self, candidate):
        edition = candidate.edition_id
        if edition in self.editions:
            return self.editions[edition]
        data = _get("GetClinrec", deadline=self.deadline, id=edition)
        population = POPULATIONS.get(int(data["adult"]) + 2 * int(data["child"]))
        if (
            data["id"] != edition
            or data["status"] != 0
            or data["apply_status"] != "Применяется"
            or data["name"] != candidate.title
            or population != candidate.population
        ):
            raise ValueError("Паспорт КР расходится с каталогом Минздрава.")
        segments = []
        for section in data["obj"]["sections"]:
            if not re.match(r"^3(?:\.|\s)", section["title"] or "") or not section["content"]:
                continue
            parser = _Paragraphs()
            parser.feed(section["content"])
            parser.close()
            parser.flush()
            segments.extend(
                TreatmentSegment(f"{section['title']}, абз. {i}", text)
                for i, text in enumerate(parser.paragraphs, 1)
            )
        if not segments:
            raise ValueError("Раздел лечения КР отсутствует.")
        now = datetime.now(UTC).isoformat()
        rec = ClinicalRecommendation(
            candidate.page_url,
            data["name"],
            edition,
            data["publish_date"][:4],
            population,
            tuple(x.strip() for x in data["mkb"].split(",")),
            "действует",
            data["apply_status"],
            "Рубрикатор Минздрава",
            candidate.primary_url,
            tuple(segments),
            now,
        )
        self.editions[edition] = CachedRecommendation(rec, "FETCHED", now)
        return self.editions[edition]


class SelectionError(ValueError):
    def __init__(self, status, message, missing=()):
        super().__init__(message)
        self.status = status
        self.missing = missing


def select_edition(sources, candidates, *, code, age, edition):
    """Проверить применимость и паспорт выбранной редакции до извлечения цитат."""
    if not code:
        raise SelectionError("NEEDS_CONTEXT", "Для поиска КР нужен код МКБ-10.", ("context.icd10",))
    selected = [c for c in candidates if not edition or c.edition_id == edition]
    if len(selected) != 1:
        raise SelectionError(
            "NEEDS_CONTEXT",
            "Не выбрана единственная применимая редакция КР.",
            ("age_group", "recommendation_edition"),
        )
    candidate = selected[0]
    if candidate.cache_state == "STALE_CACHE":
        raise SelectionError("NEEDS_REVIEW", "Актуальность каталога КР требует сверки.")
    population = candidate.population.casefold()
    if (
        not age
        and population not in {"взрослые, дети", "дети, взрослые"}
        or age
        and age.casefold() not in population
    ):
        raise SelectionError("NEEDS_CONTEXT", "Возрастная группа КР не подтверждена.", ("age_group",))
    cached = sources.load(candidate)
    rec = cached.recommendation
    if (
        cached.cache_state == "STALE_FALLBACK"
        or not rec.is_current_ministry_copy
        or rec.edition_id != candidate.edition_id
        or rec.primary_url != candidate.primary_url
        or rec.title != candidate.title
        or rec.page_url != candidate.page_url
        or rec.population.casefold() != population
        or not {code, code.split(".")[0]}.intersection(rec.icd10)
    ):
        raise SelectionError("NEEDS_REVIEW", "Паспорт или актуальность редакции КР требуют проверки.")
    return cached
