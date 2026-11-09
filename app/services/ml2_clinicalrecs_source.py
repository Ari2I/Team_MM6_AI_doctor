"""Проверенное получение раздела лечения с выбранного портала КР.

Страница должна быть копией рекомендации Минздрава: мы сверяем паспорт,
актуальный статус и ссылку на редакцию в рубрикаторе Минздрава. Сохраняем
только публичную страницу рекомендации; данные заявки в источник не отправляем.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.services.ml2_classifier import EvidenceFragment
from app.services.official_reference import inn_key

BASE_URL = "https://xn--80ahcaaarabfanddhyjjq1br6ji.xn--p1ai"
SOURCE_HOST = "xn--80ahcaaarabfanddhyjjq1br6ji.xn--p1ai"
OFFICIAL_HOST = "cr.minzdrav.gov.ru"
USER_AGENT = "Vrach-AI-ML2/1.0"
DEFAULT_CACHE = Path(__file__).resolve().parents[2] / ".local" / "official-inputs" / "clinicalrecs"
MAX_PAGE_BYTES = 8 * 1024 * 1024
_EDITION = re.compile(r"\d+_\d+")
_ICD = re.compile(r"\b[A-ZА-ЯЁ][0-9]{2}(?:\.[0-9]{1,4})?\b", re.I)
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())


class ClinicalSourceError(ValueError):
    """Невозможно безопасно использовать страницу клинической рекомендации."""


@dataclass(frozen=True)
class TreatmentSegment:
    locator: str
    text: str


@dataclass(frozen=True)
class ClinicalRecommendation:
    page_url: str
    title: str
    edition_id: str
    year: str
    population: str
    icd10: tuple[str, ...]
    status: str
    application_status: str
    source_label: str
    primary_url: str
    segments: tuple[TreatmentSegment, ...]
    retrieved_at: str

    @property
    def is_current_ministry_copy(self) -> bool:
        return (
            "рубрикатор" in self.source_label.casefold()
            and "минздрав" in self.source_label.casefold()
            and self.status.casefold() in {"действует", "действующая"}
            and self.application_status.casefold() in {"применяется", "действует"}
        )

    @property
    def version(self) -> str:
        return f"КР {self.edition_id}, {self.year}"


@dataclass(frozen=True)
class CachedRecommendation:
    recommendation: ClinicalRecommendation
    cache_state: str
    checked_at: str


@dataclass(frozen=True)
class CatalogCandidate:
    """Одна активная рекомендация Минздрава из каталога пользователя."""

    title: str
    edition_id: str
    published_at: str
    population: str
    icd10: tuple[str, ...]
    page_url: str
    primary_url: str
    cache_state: str = "FRESH_CACHE"


class _CatalogParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: dict[int, list[str]] = {}
        self.active: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = dict(attrs)
        match = re.fullmatch(r"krdata(\d+)", attributes.get("id") or "")
        self.active = int(match.group(1)) if match else None
        if self.active is not None:
            self.parts.setdefault(self.active, [])

    def handle_data(self, data: str) -> None:
        if self.active is not None:
            self.parts[self.active].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.active = None


def recommendations_for_diagnosis(
    diagnosis_code: str,
    *,
    age_group: str | None = None,
    cache_dir: Path = DEFAULT_CACHE,
    max_age: timedelta = timedelta(hours=24),
) -> tuple[CatalogCandidate, ...]:
    """Найти кандидатов в RU-каталоге по МКБ-10; не выбирать среди неоднозначных самовольно."""

    code = diagnosis_code.strip().upper().translate(str.maketrans("АВСЕНКМОРТХУ", "ABCEHKMOPTXY"))
    if not re.fullmatch(r"[A-Z][0-9]{2}(?:\.[0-9]{1,4})?", code):
        raise ValueError("Для поиска рекомендации нужен код МКБ-10.")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "catalog-ru.json"
    now = datetime.now(UTC)
    cached = None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(cached["checked_at"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        cached = None
        checked = datetime.min.replace(tzinfo=UTC)
    html: str | None = cached.get("html") if cached else None
    stale = cached is not None and now - checked > max_age
    if html is None or stale:
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
        if cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached and cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]
        try:
            with httpx.Client(
                headers=headers, timeout=httpx.Timeout(20, read=45), follow_redirects=True
            ) as client:
                if not _robots_allows(client, BASE_URL + "/"):
                    raise ClinicalSourceError("robots.txt запрещает загрузку каталога.")
                response = client.get(BASE_URL + "/")
                if response.status_code == 304 and cached:
                    html = cached["html"]
                    cached["checked_at"] = now.isoformat()
                    _atomic_write(
                        cache_path, json.dumps(cached, ensure_ascii=False).encode("utf-8")
                    )
                else:
                    response.raise_for_status()
                    if (response.url.host or "").encode("idna").decode(
                        "ascii"
                    ).casefold() != SOURCE_HOST:
                        raise ClinicalSourceError("Каталог перенаправлен на неожиданный адрес.")
                    if "html" not in response.headers.get("content-type", "").casefold():
                        raise ClinicalSourceError("Каталог имеет неожиданный формат.")
                    raw = response.content
                    if len(raw) > MAX_PAGE_BYTES:
                        raise ClinicalSourceError("Размер каталога превышает допустимый предел.")
                    html = raw.decode("utf-8")
                    cached = {
                        "checked_at": now.isoformat(),
                        "etag": response.headers.get("etag"),
                        "last_modified": response.headers.get("last-modified"),
                        "html": html,
                    }
                    _atomic_write(
                        cache_path, json.dumps(cached, ensure_ascii=False).encode("utf-8")
                    )
        except (OSError, UnicodeError, httpx.HTTPError, ClinicalSourceError):
            if not cached:
                raise
            html = cached.get("html")
            stale = True
    if not html:
        raise ClinicalSourceError("В кэше каталога нет пригодной HTML-страницы.")

    parser = _CatalogParser()
    parser.feed(html)
    parser.close()
    if len(parser.parts) < 11:
        raise ClinicalSourceError("Не все блоки каталога рекомендаций найдены.")
    try:
        packed = "".join("".join(parser.parts[number]).strip() for number in sorted(parser.parts))
        data = json.loads(gzip.decompress(base64.b64decode(packed, validate=True)))
        slug_match = re.search(r"const\s+KRP\s*=\s*(\{.*?\})\s*;\s*const\s+krPage", html, re.S)
        slugs = json.loads(slug_match.group(1)) if slug_match else {}
    except (
        ValueError,
        UnicodeError,
        OSError,
        json.JSONDecodeError,
        gzip.BadGzipFile,
        KeyError,
        TypeError,
    ):
        raise ClinicalSourceError("Не удалось проверить данные каталога рекомендаций.") from None
    population_names = {1: "Взрослые", 2: "Дети", 3: "Взрослые, дети"}
    results = []
    parent_code = code.split(".", 1)[0]
    for row in data.get("RU", []):
        if not isinstance(row, list) or len(row) < 7:
            continue
        edition = str(row[1])
        codes = tuple(
            dict.fromkeys(
                [
                    *(str(row[6]).split(",") if row[6] else []),
                    *(str(row[9]).split(",") if len(row) > 9 and row[9] else []),
                ]
            )
        )
        # A recommendation's group code covers a more specific ICD-10 code.
        if code not in codes and parent_code not in codes:
            continue
        base_id = edition.split("_", 1)[0]
        slug = slugs.get(base_id)
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-]+-" + re.escape(base_id), slug):
            continue
        population = population_names.get(row[3], "")
        if age_group and age_group.casefold() not in population.casefold():
            continue
        page_url = f"{BASE_URL}/kr/{slug}"
        results.append(
            CatalogCandidate(
                title=str(row[0]),
                edition_id=edition,
                published_at=str(row[2]),
                population=population,
                icd10=codes,
                page_url=page_url,
                primary_url=f"https://{OFFICIAL_HOST}/preview-cr/{edition}",
                cache_state="STALE_CACHE" if stale else "FRESH_CACHE",
            )
        )
    return tuple(
        sorted(
            results,
            key=lambda candidate: (candidate.published_at, candidate.edition_id),
            reverse=True,
        )
    )


class _RecommendationParser(HTMLParser):
    """Извлечь паспорт, ссылку на Минздрав и семантические блоки лечения."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict[str, str | None]]] = []
        self.title_parts: list[str] = []
        self.fields: dict[str, str] = {}
        self.source_parts: list[str] = []
        self.primary_urls: set[str] = set()
        self.segments: list[TreatmentSegment] = []
        self.in_passport = False
        self.meta_mode: str | None = None
        self.meta_key: list[str] = []
        self.meta_value: list[str] = []
        self.source_active = False
        self.h1_active = False
        self.h2_parts: list[str] | None = None
        self.h3_parts: list[str] | None = None
        self.heading = ""
        self.in_treatment = False
        self.active_locator = ""
        self.active_parts: list[str] = []
        self.segment_number = 0

    @staticmethod
    def _normal(value: str) -> str:
        return " ".join(value.split())

    def _finalize_segment(self) -> None:
        text = self._normal(" ".join(self.active_parts))
        if text:
            self.segments.append(TreatmentSegment(self.active_locator, text[:50000]))
        self.active_locator = ""
        self.active_parts = []

    def _start_segment(self, anchor: str = "") -> None:
        self._finalize_segment()
        self.segment_number += 1
        tail = anchor or f"фрагмент {self.segment_number}"
        self.active_locator = " / ".join(
            part for part in ("Раздел 3. Лечение", self.heading, tail) if part
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag not in _VOID:
            self.stack.append((tag, attributes))
        if tag == "h1":
            self.h1_active = True
        if tag == "dl" and "krt-pass" in classes:
            self.in_passport = True
        elif self.in_passport and tag == "dt":
            self.meta_mode = "key"
            self.meta_key = []
        elif self.in_passport and tag == "dd":
            self.meta_mode = "value"
            self.meta_value = []
        if tag == "p" and "krt-srcl" in classes:
            self.source_active = True
        if tag == "a":
            href = attributes.get("href") or ""
            match = re.fullmatch(
                r"https://cr\.minzdrav\.gov\.ru/preview-cr/(\d+_\d+)/?", href, re.I
            )
            if match:
                self.primary_urls.add(f"https://{OFFICIAL_HOST}/preview-cr/{match.group(1)}")
        if tag == "h2":
            self.h2_parts = []
        if tag == "h3" and self.in_treatment:
            self._finalize_segment()
            self.h3_parts = []
        if tag == "ul" and self.in_treatment and "krt-r" in classes:
            self._start_segment(attributes.get("id") or "рекомендация")
        elif (
            tag == "div"
            and self.in_treatment
            and {"r", "t-rec"} <= classes
            and not self.active_parts
        ):
            self._start_segment(attributes.get("id") or "таблица рекомендаций")

    def handle_endtag(self, tag: str) -> None:
        close_index = next(
            (i for i in range(len(self.stack), 0, -1) if self.stack[i - 1][0] == tag), None
        )
        if close_index is None:
            return
        _, attrs = self.stack[close_index - 1]
        classes = set((attrs.get("class") or "").split())

        if tag == "h1":
            self.h1_active = False
        if self.in_passport and tag in {"dt", "dd"}:
            if tag == "dt":
                self.meta_key = [self._normal(" ".join(self.meta_key))]
                self.meta_mode = None
            else:
                key = self._normal(" ".join(self.meta_key)).rstrip(":： ")
                value = self._normal(" ".join(self.meta_value))
                if key and value:
                    self.fields[key] = value
                self.meta_mode = None
        if tag == "dl" and "krt-pass" in classes:
            self.in_passport = False
        if tag == "p" and "krt-srcl" in classes:
            self.source_active = False
        if tag == "h3" and self.h3_parts is not None:
            self.heading = self._normal(" ".join(self.h3_parts))
            self.h3_parts = None
        if tag == "h2" and self.h2_parts is not None:
            heading = self._normal(" ".join(self.h2_parts))
            self.h2_parts = None
            if "лечение" in heading.casefold():
                self.in_treatment = True
                self.heading = heading
            elif self.in_treatment:
                self._finalize_segment()
                self.in_treatment = False
        del self.stack[close_index - 1 :]

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self.h1_active:
            self.title_parts.append(text)
        if self.meta_mode == "key":
            self.meta_key.append(text)
        elif self.meta_mode == "value":
            self.meta_value.append(text)
        if self.source_active:
            self.source_parts.append(text)
        if self.h2_parts is not None:
            self.h2_parts.append(text)
        elif self.h3_parts is not None:
            self.h3_parts.append(text)
        elif self.in_treatment:
            if not self.active_locator:
                self.segment_number += 1
                self.active_locator = " / ".join(
                    part
                    for part in (
                        "Раздел 3. Лечение",
                        self.heading,
                        f"фрагмент {self.segment_number}",
                    )
                    if part
                )
            if any(tag in {"script", "style", "noscript"} for tag, _ in self.stack):
                return
            self.active_parts.append(text)


def _source_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").encode("idna").decode("ascii").casefold()
    if (
        parsed.scheme != "https"
        or host != SOURCE_HOST
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or not parsed.path.startswith("/kr/")
    ):
        raise ClinicalSourceError(
            "Ожидается HTTPS-страница конкретной рекомендации на выбранном портале."
        )
    return f"https://{SOURCE_HOST}{parsed.path}"


def parse_recommendation(
    html: str, page_url: str, *, retrieved_at: str | None = None
) -> ClinicalRecommendation:
    """Проверить паспорт страницы и разложить раздел «Лечение» на цитируемые блоки."""

    canonical_url = _source_url(page_url)
    parser = _RecommendationParser()
    parser.feed(html)
    parser.close()
    parser._finalize_segment()
    title = " ".join(parser.title_parts).strip()
    source_label = " ".join(parser.source_parts).strip()
    primary_urls = sorted(parser.primary_urls)
    edition_field = parser.fields.get("ID")
    if not title or not parser.segments:
        raise ClinicalSourceError("В странице не найден заголовок или раздел лечения.")
    if "рубрикатор" not in source_label.casefold() or "минздрав" not in source_label.casefold():
        raise ClinicalSourceError("Страница не подтверждает, что копирует рубрикатор Минздрава.")
    if len(primary_urls) != 1:
        raise ClinicalSourceError("Нужна ровно одна ссылка на редакцию в рубрикаторе Минздрава.")
    primary_url = primary_urls[0]
    primary_id = primary_url.rsplit("/", 1)[-1]
    if _EDITION.fullmatch(primary_id) is None or (edition_field and edition_field != primary_id):
        raise ClinicalSourceError("ID в паспорте не совпадает со ссылкой на первоисточник.")
    diagnosis_label = next(
        (
            value
            for key, value in parser.fields.items()
            if "кодирован" in key.casefold() or "мкб" in key.casefold()
        ),
        "",
    )
    icd10 = tuple(
        dict.fromkeys(
            code.upper().translate(str.maketrans("АВСЕНКМОРТХУ", "ABCEHKMOPTXY"))
            for code in _ICD.findall(diagnosis_label)
        )
    )
    if not icd10:
        raise ClinicalSourceError("В паспорте рекомендации не найден код МКБ-10.")
    year = parser.fields.get("Год утверждения", "")
    return ClinicalRecommendation(
        page_url=canonical_url,
        title=title,
        edition_id=primary_id,
        year=year,
        population=parser.fields.get("Возрастная категория", parser.fields.get("Возраст", "")),
        icd10=icd10,
        status=parser.fields.get("Статус", ""),
        application_status=parser.fields.get("Статус применения", ""),
        source_label=source_label,
        primary_url=primary_url,
        segments=tuple(parser.segments),
        retrieved_at=retrieved_at or datetime.now(UTC).isoformat(),
    )


def _cache_files(cache_dir: Path, page_url: str) -> tuple[Path, Path]:
    key = sha256(_source_url(page_url).encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"{key}.html", cache_dir / f"{key}.json"


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_cache(html_path: Path, meta_path: Path) -> tuple[str, dict] | None:
    try:
        html = html_path.read_text(encoding="utf-8")
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if sha256(html.encode("utf-8")).hexdigest() != metadata.get("sha256"):
            return None
        return html, metadata
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _robots_allows(client: httpx.Client, page_url: str) -> bool:
    robots_url = f"{BASE_URL}/robots.txt"
    response = client.get(robots_url)
    response.raise_for_status()
    parser = RobotFileParser(robots_url)
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, page_url)


def fetch_recommendation(
    page_url: str,
    *,
    cache_dir: Path = DEFAULT_CACHE,
    max_age: timedelta = timedelta(hours=24),
) -> CachedRecommendation:
    """Использовать суточный кэш, перепроверять страницу и при сбое отдавать старую копию."""

    page_url = _source_url(page_url)
    html_path, meta_path = _cache_files(cache_dir, page_url)
    cached = _read_cache(html_path, meta_path)
    now = datetime.now(UTC)
    if cached:
        html, metadata = cached
        try:
            checked = datetime.fromisoformat(metadata["checked_at"])
        except (KeyError, TypeError, ValueError):
            checked = datetime.min.replace(tzinfo=UTC)
        if now - checked <= max_age:
            rec = parse_recommendation(html, page_url, retrieved_at=metadata.get("retrieved_at"))
            return CachedRecommendation(rec, "FRESH_CACHE", checked.isoformat())

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    if cached:
        _, metadata = cached
        if metadata.get("etag"):
            headers["If-None-Match"] = metadata["etag"]
        if metadata.get("last_modified"):
            headers["If-Modified-Since"] = metadata["last_modified"]
    try:
        with httpx.Client(
            headers=headers, timeout=httpx.Timeout(20, read=45), follow_redirects=True
        ) as client:
            if not _robots_allows(client, page_url):
                raise ClinicalSourceError("robots.txt запрещает загрузку этой страницы.")
            with client.stream("GET", page_url) as response:
                if response.status_code == 304 and cached:
                    html, metadata = cached
                    metadata["checked_at"] = now.isoformat()
                    _atomic_write(
                        meta_path, json.dumps(metadata, ensure_ascii=False).encode("utf-8")
                    )
                    rec = parse_recommendation(
                        html, page_url, retrieved_at=metadata.get("retrieved_at")
                    )
                    return CachedRecommendation(rec, "REVALIDATED", now.isoformat())
                response.raise_for_status()
                final_host = (response.url.host or "").encode("idna").decode("ascii").casefold()
                if (
                    final_host != SOURCE_HOST
                    or "html" not in response.headers.get("content-type", "").casefold()
                ):
                    raise ClinicalSourceError(
                        "Страница перенаправлена или имеет неожиданный формат."
                    )
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_PAGE_BYTES:
                        raise ClinicalSourceError("Размер страницы КР превышает допустимый предел.")
                raw = bytes(chunks)
                html = raw.decode("utf-8")
                rec = parse_recommendation(html, page_url, retrieved_at=now.isoformat())
                metadata = {
                    "url": page_url,
                    "retrieved_at": now.isoformat(),
                    "checked_at": now.isoformat(),
                    "etag": response.headers.get("etag"),
                    "last_modified": response.headers.get("last-modified"),
                    "sha256": sha256(html.encode("utf-8")).hexdigest(),
                }
                _atomic_write(html_path, html.encode("utf-8"))
                _atomic_write(meta_path, json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
                return CachedRecommendation(rec, "DOWNLOADED", now.isoformat())
    except (OSError, UnicodeError, httpx.HTTPError, ClinicalSourceError):
        if not cached:
            raise
        html, metadata = cached
        rec = parse_recommendation(html, page_url, retrieved_at=metadata.get("retrieved_at"))
        return CachedRecommendation(rec, "STALE_FALLBACK", metadata.get("checked_at", ""))


def fragments_for_inn(
    recommendation: ClinicalRecommendation,
    inn: str,
    *,
    max_fragments: int = 20,
    window_chars: int = 1000,
) -> tuple[EvidenceFragment, ...]:
    """Вернуть все проверяемые окна по МНН с оригинальной ссылкой и URL копии."""

    if not recommendation.is_current_ministry_copy:
        return ()
    if not 1 <= max_fragments <= 20 or not 200 <= window_chars <= 3000:
        raise ValueError("Лимит фрагментов или контекста вне допустимого диапазона.")
    tokens = re.findall(r"[a-zа-яё0-9-]+", inn_key(inn))
    patterns = [
        re.compile(r"(?<!\w)" + re.escape(token) + r"[а-яё]{0,4}(?!\w)", re.I)
        for token in tokens
        if len(token) >= 4
    ]
    if not patterns:
        return ()

    source_id = sha256(
        f"{recommendation.primary_url}\0{recommendation.version}".encode()
    ).hexdigest()[:24]
    found: list[EvidenceFragment] = []
    seen: set[str] = set()
    for segment in recommendation.segments:
        searchable = segment.text.replace("ё", "е").replace("Ё", "Е")
        spans: list[tuple[int, int]] = []
        for pattern in patterns:
            for match in pattern.finditer(searchable):
                spans.append(
                    (
                        max(0, match.start() - window_chars // 2),
                        min(len(segment.text), match.end() + window_chars // 2),
                    )
                )
        merged: list[list[int]] = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in merged:
            quote = segment.text[start:end].strip()
            fragment_id = sha256(
                f"{recommendation.primary_url}\0{segment.locator}\0{start}\0{quote}".encode()
            ).hexdigest()[:24]
            if fragment_id in seen:
                continue
            seen.add(fragment_id)
            found.append(
                EvidenceFragment(
                    fragment_id=fragment_id,
                    source_id=source_id,
                    title=recommendation.title,
                    version=recommendation.version,
                    url=recommendation.primary_url,
                    location=segment.locator,
                    text=quote,
                    copy_url=recommendation.page_url,
                )
            )
            if len(found) >= max_fragments:
                return tuple(found)
    return tuple(found)
