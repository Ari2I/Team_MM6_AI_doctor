"""Smoke через nginx; запускается внутри api без дополнительных зависимостей."""

import base64
import json
import os
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from openpyxl import load_workbook

from app.config import get_access_settings

BASE = "http://web:80"
DATA = Path("tests/data")
REFERENCE_VERSION = os.environ.get("SMOKE_REFERENCE_VERSION", "demo-api-1")
USER = os.environ.get("WEB_USER", "")
PASSWORD = os.environ.get("WEB_PASSWORD", "")
TOKEN = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode() if USER and PASSWORD else None


def request(
    path: str, data: bytes | None = None, content_type: str | None = None, *, auth: bool = True
) -> tuple[int, bytes, str]:
    headers = {"Authorization": f"Basic {TOKEN}"} if auth and TOKEN else {}
    if content_type:
        headers["Content-Type"] = content_type
    req = Request(BASE + path, data=data, headers=headers)
    try:
        response = urlopen(req, timeout=60)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read(), response.headers.get("Content-Type", "")


def upload(content: bytes) -> tuple[int, dict]:
    boundary = uuid4().hex
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="sample.xlsx"\r\n'
            "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            "\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, raw, _ = request("/applications", body, f"multipart/form-data; boundary={boundary}")
    return status, json.loads(raw)


def variant(*, typo: bool = False) -> bytes:
    # Temporary variants of the existing fixture, never overwrite tests/data.
    workbook = load_workbook(DATA / "sample_ok.xlsx")
    sheet = workbook.active
    assert sheet is not None
    if typo:
        sheet["A2"] = "Нурафен"
    else:
        sheet["E2"] = None
        sheet.delete_rows(5, 3)
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def access() -> None:
    assert request("/health", auth=False)[0] == 200
    settings = get_access_settings()
    paths = {
        "/": 200,
        "/index.html": 200,
        "/applications": 200 if settings.enable_application_list else 404,
        "/applications/": 200 if settings.enable_application_list else 404,
        "/docs": 200 if settings.enable_api_docs else 404,
        "/applications/00000000-0000-0000-0000-000000000000/report.xlsx": 404,
    }
    for path, open_status in paths.items():
        assert request(path, auth=False)[0] == (401 if TOKEN else open_status), path
    assert request("/applications", b"", auth=False)[0] == (401 if TOKEN else 422)
    assert request("/")[0] == 200
    status, html, _ = request("/")
    assert status == 200
    import re

    assets = re.findall(rb'(?:src|href)="(/assets/[^\"]+)"', html)
    assert assets
    for asset in assets:
        assert request(asset.decode(), auth=False)[0] == (401 if TOKEN else 200)
        assert request(asset.decode())[0] == 200
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert request(path)[0] == (200 if settings.enable_api_docs else 404), path
    for path in ("/applications", "/applications/"):
        assert request(path)[0] == (200 if settings.enable_application_list else 404), path


def analyzed(content: bytes) -> dict:
    status, card = upload(content)
    assert status == 201
    assert card["application"]["status"] == "analyzed"
    assert card["disclaimer"] and card["items"]
    for item in card["items"]:
        assert item["checks"]
        assert all(check["reference_version"] == REFERENCE_VERSION for check in item["checks"])
    application_id = card["application"]["id"]
    status, saved, _ = request(f"/applications/{application_id}")
    assert status == 200 and json.loads(saved) == card
    status, report, content_type = request(f"/applications/{application_id}/report.xlsx")
    assert status == 200 and "spreadsheetml" in content_type
    workbook = load_workbook(BytesIO(report))
    assert {"Сводка", "Препараты", "Предупреждения"} <= set(workbook.sheetnames)
    workbook.close()
    return card


def correct() -> None:
    card = analyzed((DATA / "sample_ok.xlsx").read_bytes())
    assert all(item["mnn"] for item in card["items"])


def typo() -> None:
    card = analyzed(variant(typo=True))
    item = card["items"][0]
    assert item["mnn"] is None
    assert all(check["needs_review"] for check in item["checks"])
    assert card["application"]["requires_review"]


def two_diagnoses() -> None:
    card = analyzed(variant())
    assert len(card["diagnoses"]) == 2
    assert len(card["items"][0]["checks"]) == 2


def corrupt() -> None:
    status, body = upload((DATA / "sample_corrupt.xlsx").read_bytes())
    assert status == 422 and body["errors"][0]["code"] == "invalid_excel"


def demo_files() -> None:
    cases = [
        ("correct.xlsx", "green", 0, set(), 3),
        ("typo.xlsx", "yellow", 1, {"DRUG_AMBIGUOUS"}, 3),
        ("unknown.xlsx", "undetermined", 1, {"DRUG_NOT_FOUND"}, 2),
        ("two_diagnoses.xlsx", "yellow", 2, {"NO_RULE_FOR_PAIR"}, 4),
    ]
    assert {path.name for path in Path("data/demo").glob("*.xlsx")} == {case[0] for case in cases}
    for filename, color, review_count, reasons, check_count in cases:
        card = analyzed((Path("data/demo") / filename).read_bytes())
        assert card["application"]["traffic_light"] == color
        assert card["application"]["requires_review"] == bool(review_count)
        assert card["application"]["category_shares"]["total_amount"] == 1000
        items = card["items"]
        assert sum(any(c["needs_review"] for c in i["checks"]) for i in items) == review_count
        checks = [check for item in items for check in item["checks"]]
        assert len(checks) == check_count
        assert {c["review_reason"] for c in checks if c["needs_review"]} == reasons
        assert all(c["source"] for c in checks if not c["needs_review"])
        if filename == "two_diagnoses.xlsx":
            assert len(card["diagnoses"]) == 2 and len(items[0]["checks"]) == 2
        print(f"OK demo-api-2 {filename}", flush=True)


legacy_scenarios = [
    ("access and static assets", access),
    ("correct + card + report", correct),
    ("typo + review + report", typo),
    ("two diagnoses + pair checks + report", two_diagnoses),
    ("corrupt file", corrupt),
]
scenarios = legacy_scenarios if REFERENCE_VERSION == "demo-api-1" else [("demo files", demo_files)]
assert REFERENCE_VERSION in {"demo-api-1", "demo-api-2"}
failed = False
for name, scenario in scenarios:
    try:
        scenario()
        print(f"OK {name}", flush=True)
    except Exception as error:
        # Do not print request objects, response bodies, or credentials.
        print(f"FAIL {name}: {type(error).__name__}", flush=True)
        failed = True
raise SystemExit(1 if failed else 0)
