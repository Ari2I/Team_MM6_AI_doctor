"""Индекс разделов лечения из опубликованных копий ClinReky."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import re
from threading import Lock
from time import monotonic, sleep
from urllib.parse import parse_qs, urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx

BASE = "https://clinreky.ru"
AGENT = "Vrach-reference/1.0"
_SLUG = re.compile(r"[A-Za-z0-9._-]+")
_EDITION = re.compile(r"[0-9]+_[0-9]+")
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_MAX_SIZE = 10_000_000


@dataclass
class PublishedCopy:
    url: str
    segments: list[tuple[str, str]]


def _words(value: str) -> list[str]:
    return re.findall(r"\w+", value.casefold().replace("ё", "е"))


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: dict[str, set[str]] = {"view": set(), "pdf": set()}
        self.title: list[str] = []
        self.segments: list[tuple[str, str]] = []
        self.stack: list[tuple[str, str, bool]] = []
        self.treatment: int | None = None
        self.capture: tuple[int, str, list[str]] | None = None
        self.heading: int | None = None

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        anchor = attributes.get("id") or ""
        self.stack.append((tag, attributes.get("data-num") or "", tag in {"script", "style", "noscript"}))
        depth = len(self.stack)
        if tag == "h1":
            self.heading = depth
        if tag == "a" and "krv2-pdf-link" in classes:
            self._official_link(attributes.get("href") or "")
        if tag == "details" and anchor == "sec-treatment":
            self.treatment = depth
        if self.treatment is None or self.capture is not None:
            return
        if "krv2-rec-original" in classes or (tag == "p" and anchor.startswith("b-treatment-")):
            number = next((number for _, number, _ in reversed(self.stack)
                           if re.fullmatch(r"3(?:\.[0-9]+)*", number)), "3")
            self.capture = depth, f"Раздел {number}", []

    def _official_link(self, href: str) -> None:
        url = urlsplit(href)
        if url.scheme != "https" or url.username or url.password:
            return
        if url.hostname == "cr.minzdrav.gov.ru":
            match = re.fullmatch(r"/(?:view-cr|preview-cr)/([0-9]+_[0-9]+)/?", url.path)
            if match:
                self.links["view"].add(match[1])
        if url.hostname == "apicr.minzdrav.gov.ru" and url.path.casefold() == "/api.ashx":
            args = parse_qs(url.query)
            identifiers = args.get("id", [])
            if args.get("op") == ["GetClinrecPdf"] and len(identifiers) == 1 and _EDITION.fullmatch(identifiers[0]):
                self.links["pdf"].add(identifiers[0])

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        closing = next((i for i in range(len(self.stack), 0, -1) if self.stack[i - 1][0] == tag), None)
        if closing is None:
            return
        if self.capture and self.capture[0] >= closing:
            _, label, parts = self.capture
            text = " ".join(" ".join(parts).split())
            if text:
                self.segments.append((label, text[:50000]))
            self.capture = None
        if self.heading is not None and self.heading >= closing:
            self.heading = None
        if self.treatment is not None and self.treatment >= closing:
            self.treatment = None
        del self.stack[closing - 1:]

    def handle_data(self, data):
        if not (self.capture or self.heading) or any(skip for _, _, skip in self.stack):
            return
        if self.heading:
            self.title.append(data)
        if self.capture:
            self.capture[2].append(data)


def read_copies(directory: Path, titles: dict[str, str]) -> dict[str, PublishedCopy]:
    """Проверить обе ссылки на редакцию и заголовок по сохранённому каталогу Минздрава."""
    copies = {}
    for path in sorted(directory.glob("*.html")):
        if not _SLUG.fullmatch(path.stem) or path.stat().st_size > _MAX_SIZE:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            # У страницы есть отдельное оглавление, краткий режим и приложения.
            # Парсеру нужны только метаданные до первого раздела и исходный раздел лечения.
            sections = list(re.finditer(r'<details\s+id="sec-[^"]+"', raw))
            treatment = next((i for i, match in enumerate(sections)
                              if match.group().endswith('"sec-treatment"')), None)
            if treatment is None:
                continue
            start = sections[treatment].start()
            end = sections[treatment + 1].start() if treatment + 1 < len(sections) else len(raw)
            page = _Page()
            page.feed(raw[:sections[0].start()] + raw[start:end])
            page.close()
        except (OSError, UnicodeError, ValueError):
            continue
        if len(page.links["view"]) != 1 or page.links["view"] != page.links["pdf"] or not page.segments:
            continue
        key = next(iter(page.links["view"]))
        if key not in titles:
            continue
        title = re.sub(r"\s*[—–-]\s*КР\s*\d+\s*$", "", " ".join(page.title), flags=re.I)
        if _words(title) != _words(titles[key]):
            continue
        copies.setdefault(key, PublishedCopy(f"{BASE}/nosology/{path.stem}/", page.segments))
    return copies


def download_copies(cache: Path) -> None:
    """Обойти публичную карту сайта независимо от заявок, соблюдая правила сайта."""
    from app.services.official_catalog import _write

    cache.mkdir(parents=True, exist_ok=True)
    robots = RobotFileParser()
    with httpx.Client(headers={"User-Agent": AGENT}, timeout=12, follow_redirects=False) as client:
        robots.parse(client.get(f"{BASE}/robots.txt").raise_for_status().text.splitlines())
        sitemap = ElementTree.fromstring(client.get(f"{BASE}/sitemap.xml").raise_for_status().content)
        urls = sorted({item.text.strip() for item in sitemap.iter()
                       if item.tag.split("}")[-1] == "loc" and item.text
                       and item.text.strip().startswith(BASE + "/nosology/")
                       and robots.can_fetch(AGENT, item.text.strip())})
        clock_lock = Lock()
        scheduled = 0.0
        spacing = max(0.27, robots.crawl_delay(AGENT) or 0)

        def download(url: str) -> bool:
            nonlocal scheduled
            slug = urlsplit(url).path.removeprefix("/nosology/").strip("/")
            if not _SLUG.fullmatch(slug):
                return False
            path = cache / f"{slug}.html"
            if path.is_file():
                return True
            with clock_lock:
                scheduled = max(monotonic(), scheduled) + spacing
                start = scheduled
            sleep(max(0, start - monotonic()))
            try:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    if "html" not in response.headers.get("Content-Type", ""):
                        return False
                    chunks = bytearray()
                    for data in response.iter_bytes():
                        chunks.extend(data)
                        if len(chunks) > _MAX_SIZE:
                            return False
                _write(path, chunks)
                return True
            except httpx.HTTPError:
                return False

        count = 0
        with ThreadPoolExecutor(max_workers=4) as workers:
            for number, success in enumerate(workers.map(download, urls), 1):
                count += success
                if number % 50 == 0:
                    print(f"Копии: обработано {number}, в кэше {count}.", flush=True)
    print(f"Публичные страницы копий: в кэше {count} из {len(urls)}.")



def merge_copies(snapshot, copies: dict[str, PublishedCopy]):
    """Дополнить версию справочника, сохраняя уже доступные официальные тексты."""
    from app.services.official_reference import OfficialPage

    indexed = {page.url for page in snapshot.pages if not page.copy_url}
    replacement = {}
    documents = []
    for document in snapshot.documents:
        key = document.source.version.split(" · ", 1)[0]
        copy = copies.get(key)
        url = document.source.url
        if copy is None or url in indexed:
            documents.append(document)
            continue
        query = parse_qs(urlsplit(url).query)
        if query.get("id") != [key] or query.get("op") != ["GetClinrecPdf"]:
            documents.append(document)
            continue
        documents.append(document.model_copy(update={"state": "copy_indexed"}))
        if url not in replacement:
            replacement[url] = [
                OfficialPage(url=url, number=number, text=text, location=location, copy_url=copy.url)
                for number, (location, text) in enumerate(copy.segments, 1)
            ]
    if not replacement:
        return snapshot
    pages = [page for page in snapshot.pages if page.url not in replacement]
    for edition in replacement.values():
        pages.extend(edition)
    pending = snapshot.model_copy(update={
        "version": "pending", "documents": tuple(documents), "pages": tuple(pages),
    })
    digest = sha256(pending.model_dump_json().encode()).hexdigest()[:16]
    return pending.model_copy(update={"version": f"official-{snapshot.as_of}-{digest}"})


def main() -> None:
    from app.services.official_catalog import _read, _write, active_documents
    from app.services.official_reference import OfficialSnapshot

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True, help="Каталог с ранее выгруженным catalog.json.")
    parser.add_argument("--download", action="store_true", help="Загрузить независимый публичный каталог копий.")
    parser.add_argument("--snapshot", type=Path, help="Ранее проверенный официальный снимок.")
    parser.add_argument("--output", type=Path, help="Новый снимок для проверки перед подключением.")
    args = parser.parse_args()
    if bool(args.snapshot) != bool(args.output) or not (args.snapshot or args.download):
        parser.error("Укажите --download или оба аргумента --snapshot и --output.")
    if args.snapshot and args.snapshot.resolve() == args.output.resolve():
        parser.error("Запишите новый снимок в отдельный файл.")
    try:
        if args.download:
            download_copies(args.cache / "copies")
        if args.snapshot:
            snapshot = OfficialSnapshot.model_validate_json(args.snapshot.read_bytes())
            catalogue = {row["CodeVersion"]: row["Name"]
                         for row in active_documents(_read(args.cache / "catalog.json"))}
            copies = read_copies(args.cache / "copies", catalogue)
            merged = merge_copies(snapshot, copies)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            _write(args.output, merged.model_dump_json().encode())
            originals = {page.url for page in merged.pages if not page.copy_url}
            indexed = {page.url for page in merged.pages if page.copy_url}
            print(f"{merged.version} | оригиналы {len(originals)} | копии в индексе {len(indexed)} | "
                  f"совпавшие редакции копий {len(copies)}")
    except (OSError, ValueError, httpx.HTTPError, ElementTree.ParseError) as error:
        parser.exit(1, f"Импорт копий не выполнен. {error}\n")


if __name__ == "__main__":
    main()
