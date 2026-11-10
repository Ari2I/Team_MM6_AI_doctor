"""Загрузка актуальной выгрузки ГРЛС и локальный снимок для AI/ML №2.

Страница ГРЛС публикует ссылку на ZIP с датированной выгрузкой XLSX. Этот
модуль только обнаруживает эту ссылку, проверяет архив и передаёт его уже
импортёру собственного ML-2 пакета. В запросах заявок сайт ГРЛС не вызывается.
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urljoin, urlsplit
from zipfile import BadZipFile, ZipFile

import httpx

from .registry import build_snapshot

GRLS_PAGE = "https://grls.rosminzdrav.ru/GRLS.aspx"
GRLS_HOST = "grls.rosminzdrav.ru"
DEFAULT_CACHE = (
    Path(__file__).resolve().parents[3] / ".local" / "official-inputs" / "grls"
)
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
_ARCHIVE_MEMBER = re.compile(r"grls(\d{4}-\d{2}-\d{2})-\d+-.+\.xlsx", re.IGNORECASE)
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


class _ArchiveLinkParser(HTMLParser):
    """Извлечь ссылку из кнопки скачивания полного ZIP реестра."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[tuple[str, str]] = []
        self._button_depth: int | None = None
        self._button_url: str | None = None
        self._button_title = ""
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self._depth += 1
        if tag == "button":
            self._button_depth = self._depth
            self._button_url = None
            self._button_title = ""
            onclick = attrs_map.get("onclick") or ""
            match = re.search(r"\bgo\(\s*(['\"])(.*?)\1\s*\)", onclick, re.IGNORECASE)
            if match and "GetGRLS.ashx" in match.group(2):
                self._button_url = match.group(2)
        elif self._button_depth is not None and attrs_map.get("title"):
            self._button_title = attrs_map["title"] or self._button_title

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self._button_depth is not None:
            if self._button_url:
                self.candidates.append((self._button_url, self._button_title))
            self._button_depth = None
            self._button_url = None
            self._button_title = ""
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self._depth = max(0, self._depth - 1)


def _safe_archive_url(relative_url: str) -> str:
    url = urljoin(GRLS_PAGE, relative_url)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != GRLS_HOST
        or parsed.port is not None
        or parsed.path != "/GetGRLS.ashx"
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Страница ГРЛС вернула неожиданную ссылку на выгрузку.")
    query = parse_qs(parsed.query)
    file_ids = query.get("FileGUID", [])
    request_ids = query.get("UserReq", [])
    if (
        len(file_ids) != 1
        or _UUID.fullmatch(file_ids[0]) is None
        or len(request_ids) != 1
        or not request_ids[0].isdigit()
    ):
        raise ValueError(
            "В ссылке на выгрузку ГРЛС нет корректного идентификатора файла."
        )
    return url


def _archive_date(names: list[str]) -> date:
    dates = set()
    for name in names:
        match = _ARCHIVE_MEMBER.fullmatch(Path(name).name)
        if match:
            dates.add(date.fromisoformat(match.group(1)))
    if len(dates) != 1:
        raise ValueError("В ZIP нет единой даты выгрузки в именах XLSX-файлов ГРЛС.")
    return dates.pop()


def _validate_archive(payload: bytes) -> tuple[date, list[str]]:
    if not payload or len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP ГРЛС пуст или превышает допустимый размер.")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            if sum(member.file_size for member in archive.infolist()) > 500_000_000:
                raise ValueError("Распакованный ZIP ГРЛС превышает допустимый размер.")
            names = archive.namelist()
            if archive.testzip() is not None:
                raise ValueError("ZIP ГРЛС повреждён.")
    except BadZipFile:
        raise ValueError("Ответ сайта ГРЛС не является ZIP-архивом.") from None
    workbooks = [name for name in names if _ARCHIVE_MEMBER.fullmatch(Path(name).name)]
    if not any(
        Path(name).stem.casefold().endswith("-действующий") for name in workbooks
    ):
        raise ValueError(
            "В архиве не найден XLSX действующих регистрационных удостоверений."
        )
    return _archive_date(workbooks), workbooks


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_latest(cache: Path = DEFAULT_CACHE) -> tuple[Path, date, str]:
    """Скачать опубликованный ГРЛС ZIP, сохранив проверенный архив в локальный кэш."""

    headers = {
        "User-Agent": "Vrach-AI-ML2/1.0 (official registry import)",
        "Accept": "text/html,application/zip,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    with httpx.Client(
        headers=headers, timeout=httpx.Timeout(30, read=120), follow_redirects=True
    ) as client:
        response = client.get(GRLS_PAGE)
        response.raise_for_status()
        if len(response.content) > MAX_PAGE_BYTES:
            raise ValueError("Страница поиска ГРЛС неожиданно велика.")
        parser = _ArchiveLinkParser()
        parser.feed(response.text)
        parser.close()
        if len(parser.candidates) != 1:
            raise ValueError(
                "На странице ГРЛС не найдена единственная кнопка выгрузки полного ZIP."
            )
        relative_url, tooltip = parser.candidates[0]
        url = _safe_archive_url(relative_url)
        chunks = bytearray()
        with client.stream("GET", url) as archive_response:
            archive_response.raise_for_status()
            final_url = urlsplit(str(archive_response.url))
            if final_url.scheme != "https" or final_url.hostname != GRLS_HOST:
                raise ValueError("Скачивание ГРЛС перенаправлено на неожиданный адрес.")
            for chunk in archive_response.iter_bytes():
                chunks.extend(chunk)
                if len(chunks) > MAX_ARCHIVE_BYTES:
                    raise ValueError("ZIP ГРЛС превысил допустимый размер.")
        payload = bytes(chunks)

    as_of, _ = _validate_archive(payload)
    date_in_tooltip = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", tooltip)
    if date_in_tooltip:
        page_date = date(
            int(date_in_tooltip.group(3)),
            int(date_in_tooltip.group(2)),
            int(date_in_tooltip.group(1)),
        )
        if page_date != as_of:
            raise ValueError("Дата ZIP не совпадает с датой, указанной в кнопке ГРЛС.")

    digest = sha256(payload).hexdigest()
    target = cache / f"grls-{as_of.isoformat()}.zip"
    if target.exists() and sha256(target.read_bytes()).hexdigest() != digest:
        target = cache / f"grls-{as_of.isoformat()}-{digest[:10]}.zip"
    if not target.exists():
        _atomic_write(target, payload)
    return target, as_of, digest


def build_registry_snapshot(
    archive: Path, as_of: date, output: Path
) -> tuple[str, int, str]:
    """Импортировать только реестр и сохранить его отдельно от общего снимка команды."""

    snapshot = build_snapshot(
        archive,
        as_of,
        registry_url=GRLS_PAGE,
        coverage="registry_export",
    )
    _atomic_write(output, snapshot.model_dump_json().encode("utf-8"))
    return (
        snapshot.version,
        len(snapshot.registrations),
        sha256(output.read_bytes()).hexdigest(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help="Папка локального кэша ZIP и registry-only снимков.",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        help="Использовать скачанный архив вместо обращения к сайту.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Путь JSON-снимка; по умолчанию создаётся рядом с архивом.",
    )
    args = parser.parse_args()
    try:
        if args.archive:
            archive = args.archive
            if not archive.is_file():
                raise ValueError("Архив ГРЛС не найден.")
            if archive.stat().st_size > MAX_ARCHIVE_BYTES:
                raise ValueError("ZIP ГРЛС превысил допустимый размер.")
            as_of, _ = _validate_archive(archive.read_bytes())
        else:
            archive, as_of, _ = download_latest(args.cache)
        output = (
            args.output or args.cache / f"official-reference-{as_of.isoformat()}.json"
        )
        if archive.resolve() == output.resolve():
            raise ValueError("Снимок нельзя записывать поверх исходного ZIP-архива.")
        version, registrations, digest = build_registry_snapshot(archive, as_of, output)
    except (OSError, ValueError, httpx.HTTPError) as error:
        parser.exit(1, f"Снимок ГРЛС не создан. {error}\n")
    print(
        f"{version} | записей {registrations} | архив {archive} | снимок {output} | sha256 {digest}"
    )


if __name__ == "__main__":
    main()
