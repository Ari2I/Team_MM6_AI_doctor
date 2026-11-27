"""Загрузить только подготовленный синтетический контроль; вывод без значений ячеек."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def read_report(client: httpx.Client, identifier: str) -> dict:
    response = client.get(f"applications/{identifier}/ai-report")
    if response.status_code != 200:
        raise ValueError("AI_REPORT_NOT_SAVED")
    from app.schemas.ai_report import AIReport

    value = response.json()
    AIReport.model_validate(value)
    return value


def exercise(client: httpx.Client, folder: Path, *, reopen: bool) -> None:
    state_path = folder / "state.json"
    if reopen:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        identifier = str(UUID(state["application_id"]))
    else:
        if state_path.exists():
            raise ValueError("STATE_EXISTS")
        case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
        if case.get("schema_version") != "network-case.1" or case.get("synthetic") is not True:
            raise ValueError("NOT_SYNTHETIC")
        content = (folder / "control.xlsx").read_bytes()
        if sha256(content).hexdigest() != case["workbook_sha256"]:
            raise ValueError("WORKBOOK_CHANGED")
        response = client.post(
            "applications",
            files={"file": ("synthetic-network-control.xlsx", content)},
            data={"age_group": case["age_group"]},
        )
        if response.status_code != 201:
            raise ValueError("UPLOAD_FAILED")
        card = response.json()
        identifier = str(UUID(card["application"]["id"]))
        state = dict(schema_version="network-acceptance.1", application_id=identifier)
        # ID сохраняем даже при fallback: это позволяет безопасно проверить причины в БД.
        state_path.write_text(json.dumps(state), encoding="utf-8")
        if card["application"]["status"] != "analyzed":
            raise ValueError("ANALYSIS_FAILED")
        state["card_sha256"] = digest(card)

    report = read_report(client, identifier)
    if read_report(client, identifier) != report:
        raise ValueError("REPORT_CHANGED")
    card_response = client.get(f"applications/{identifier}")
    if card_response.status_code != 200 or digest(card_response.json()) != state["card_sha256"]:
        raise ValueError("CARD_CHANGED")
    if reopen and digest(report) != state["report_sha256"]:
        raise ValueError("REPORT_CHANGED")
    excel = client.get(f"applications/{identifier}/report.xlsx")
    if excel.status_code != 200:
        raise ValueError("EXCEL_FAILED")
    workbook = load_workbook(BytesIO(excel.content), read_only=True)
    try:
        if workbook.sheetnames != ["Сводка", "Препараты", "Предупреждения"]:
            raise ValueError("EXCEL_INVALID")
    finally:
        workbook.close()
    if not reopen:
        state["report_sha256"] = digest(report)
        state_path.write_text(json.dumps(state), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, default=ROOT / ".local/network-acceptance")
    parser.add_argument("--reopen", action="store_true")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    try:
        url = os.environ.get("WEB_URL", "")
        parsed = urlsplit(url)
        user, password = os.environ.get("WEB_USER", ""), os.environ.get("WEB_PASSWORD", "")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not user
            or not password
            or parsed.scheme == "http"
            and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("WEB_SETTINGS")
        with httpx.Client(
            base_url=url.rstrip("/") + "/",
            auth=httpx.BasicAuth(user, password),
            verify=True,
            follow_redirects=False,
            trust_env=False,
            timeout=330,
        ) as client:
            exercise(client, args.case_dir, reopen=args.reopen)
    except Exception:
        print(
            "Контроль не пройден: проверьте HTTP-доступ, "
            "сохранение отчёта и диагностические коды в БД."
        )
        return 1
    print(
        "Повторные GET карточки/AI-отчёта и Excel: OK."
        if args.reopen
        else "Синтетическая загрузка и чтение отчёта: OK. "
        "Модельные вызовы требуют проверки снимка/индекса."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
