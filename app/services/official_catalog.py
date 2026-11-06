"""Импорт каталога Минздрава и PDF независимо от данных заявок."""

import argparse
from collections import defaultdict
from datetime import date
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from time import sleep

import httpx

API = "https://apicr.minzdrav.gov.ru/api.ashx"
LIST_URL = f"{API}?op=GetJsonClinrecsFilterV2"
MKB_URL = f"{API}?op=GetMkbRefList"
PDF_URL = f"{API}?op=GetClinrecPdf"
_ID = re.compile(r"[0-9]+_[0-9]+")
_MAX_PDF = 30 * 1024 * 1024


def _write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _json(path: Path, value) -> None:
    _write(path, json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _read(path: Path):
    return json.loads(path.read_bytes())


def fetch_catalog(client: httpx.Client) -> list[dict]:
    query = {
        "filters": [{"fieldName": "status", "filterType": 1, "filterValueType": 2,
                     "value1": 0, "value2": "", "values": []}],
        "sortOption": {"fieldName": "publishdate", "sortType": 2},
        "pageSize": 100, "currentPage": 1, "useANDoperator": True, "columns": [],
    }
    records = []
    expected = None
    while expected is None or len(records) < expected:
        response = client.post(LIST_URL, json=query).raise_for_status().json()
        expected = response["TotalRecords"] if expected is None else expected
        if expected != response["TotalRecords"] or not response["Data"] or expected > 20000:
            raise ValueError("Список рекомендаций изменился во время выгрузки.")
        records.extend(response["Data"])
        query["currentPage"] += 1
    if len(records) != expected or len({row["CodeVersion"] for row in records}) != expected:
        raise ValueError("Выгрузка каталога неполна или содержит дубли.")
    return records


def active_documents(records: list[dict]):
    for row in records:
        if row.get("ApplyStatusCalculated") == 1 and _ID.fullmatch(str(row.get("CodeVersion", ""))):
            yield row


def _fetch_pdf(client: httpx.Client, key: str) -> bytes:
    with client.stream("GET", f"{PDF_URL}&id={key}") as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > _MAX_PDF:
                raise ValueError("Размер PDF превышает предел.")
    if not content.startswith(b"%PDF-"):
        raise ValueError("Полученный файл не является PDF.")
    return bytes(content)


def _temporary_error(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return isinstance(error, httpx.TransportError)


def _fetch_pdfs(client: httpx.Client, records: list[dict], cache: Path, limit: int | None) -> None:
    target = cache / "pdf"
    target.mkdir(exist_ok=True)
    failures_path = cache / "unavailable.json"
    failures = set(_read(failures_path)) if failures_path.exists() else set()
    attempts = downloaded = consecutive_errors = 0
    for row in active_documents(records):
        key = row["CodeVersion"]
        file = target / f"{key}.pdf"
        if file.exists():
            failures.discard(key)
            continue
        if limit is not None and attempts >= limit:
            break
        attempts += 1
        try:
            try:
                content = _fetch_pdf(client, key)
            except httpx.HTTPError as error:
                if not _temporary_error(error):
                    raise
                sleep(1)
                content = _fetch_pdf(client, key)
            _write(file, content)
            downloaded += 1
            failures.discard(key)
            consecutive_errors = 0
        except (httpx.HTTPError, ValueError) as error:
            failures.add(key)
            consecutive_errors = consecutive_errors + 1 if _temporary_error(error) else 0
            if consecutive_errors >= 3:
                _json(failures_path, sorted(failures))
                raise ValueError("Рубрикатор трижды не выдал PDF после повторных попыток. "
                                 "Ранее скачанные файлы сохранены.") from error
        if attempts % 25 == 0:
            print(f"PDF: получено {downloaded} из {attempts} запрошенных.", flush=True)
        sleep(0.2)
    _json(failures_path, sorted(failures))
    print(f"PDF: получено {downloaded} из {attempts} запрошенных, не получено {len(failures)}.")


def read_rubricator(cache: Path, as_of: date):
    from app.services.official_reference import ICD, DiagnosisEntry, OfficialDocument, OfficialPage, Source

    records = _read(cache / "catalog.json")
    codes = _read(cache / "mkb.json")
    failures_path = cache / "unavailable.json"
    failures = set(_read(failures_path)) if failures_path.exists() else set()
    diagnoses = []
    descendants = defaultdict(list)
    for node in codes:
        code = node["Code"].strip().upper()
        descendants[node.get("ParentId")].append(node)
        if ICD.fullmatch(code):
            diagnoses.append(DiagnosisEntry(
                code=code, diagnosis=node["Name"], source=Source(
                    title="Справочник МКБ-10 рубрикатора Минздрава", url=MKB_URL,
                    version=str(as_of), location=code, retrieved_at=as_of, quote=node["Name"],
                ),
            ))
    documents, pages = [], []

    def exact_codes(item):
        code = item["MkbCode"].strip().upper()
        if ICD.fullmatch(code):
            yield code, item["MkbName"]
        elif "-" in code:
            for child in descendants[item.get("MkbId")]:
                yield from exact_codes({"MkbCode": child["Code"], "MkbName": child["Name"], "MkbId": child["Id"]})

    for row in active_documents(records):
        key = row["CodeVersion"]
        url = f"{PDF_URL}&id={key}"
        path = cache / "pdf" / f"{key}.pdf"
        state = "unavailable" if key in failures else "pending"
        if path.is_file():
            extracted = _extract_pages(path, url)
            pages.extend(extracted)
            state = "indexed" if extracted else "no_text"
        version = f"{key} · {row['PublishDateStr'][:10]}" if row.get("PublishDateStr") else key
        source = Source(title=row["Name"], url=url, version=version, location="Рубрикатор Минздрава",
                        retrieved_at=as_of, quote=row["Name"])
        mapped = dict(pair for item in row.get("Mkbs") or [] for pair in exact_codes(item))
        for code, label in mapped.items():
            documents.append(OfficialDocument(
                code=code, diagnosis=label, source=source, state=state,
                scope="group" if len(code) == 3 else "exact",
            ))
    return diagnoses, documents, pages


def _extract_pages(path: Path, url: str):
    from app.services.official_reference import OfficialPage
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
        return [OfficialPage(url=url, number=number, text=text[:50000])
                for number, page in enumerate(reader.pages, 1)
                if (text := " ".join((page.extract_text() or "").split()))]
    except (OSError, PdfReadError):
        return []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--download-pdfs", action="store_true")
    parser.add_argument("--pdf-limit", type=int, help="Число новых PDF за один запуск.")
    parser.add_argument("--reuse-catalog", action="store_true", help="Использовать ранее загруженный каталог.")
    args = parser.parse_args()
    if args.pdf_limit is not None and args.pdf_limit < 1:
        parser.error("Укажите положительный предел числа PDF.")
    args.cache.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=20, headers={"Origin": "https://cr.minzdrav.gov.ru"}) as client:
        if args.reuse_catalog:
            records = _read(args.cache / "catalog.json")
        else:
            records = fetch_catalog(client)
            mkb = client.get(MKB_URL).raise_for_status().json()
            if not isinstance(mkb, list) or not mkb:
                raise ValueError("Пустая выгрузка МКБ-10.")
            _json(args.cache / "mkb.json", mkb)
            _json(args.cache / "catalog.json", records)
        if args.download_pdfs:
            try:
                _fetch_pdfs(client, records, args.cache, args.pdf_limit)
            except ValueError as error:
                parser.exit(1, f"{error}\n")
    print(f"Каталог: {len(records)} опубликованных записей, {sum(1 for _ in active_documents(records))} применяются.")


if __name__ == "__main__":
    main()
