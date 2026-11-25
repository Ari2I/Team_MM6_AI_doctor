"""Поэтапная проверка настоящих источников и backend без вывода заявок и секретов."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ai" / "src"))


def emit(stage: str, **data: Any) -> None:
    print(json.dumps({"stage": stage, **data}, ensure_ascii=True), flush=True)


def test_file() -> bytes:
    """Вымышленная покупка, без ФИО, дат рождения или иных идентификаторов."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Торговое наименование", "Дозировка", "Количество", "Сумма", "Диагноз"])
    sheet.append(["Сальбутамол", "100 мкг", 1, 1000, "J45.9 Бронхиальная астма"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def main() -> int:
    import httpx
    from llm_rag.ml2.clinicalrecs import (
        BASE_URL,
        fetch_recommendation,
        fragments_for_inn,
        recommendations_for_diagnosis,
    )
    from llm_rag.ml2.registry import RegistryIndex, RegistrySnapshot

    from app.config import AISettings
    from app.schemas.analysis import PairCheck
    from app.services.ai_adapter import AIAdapter, AIInput

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=os.environ.get("AI_REGISTRY_PATH"))
    parser.add_argument("--cache", type=Path, default=os.environ.get("AI_CLINICAL_CACHE_DIR"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--network-sources", action="store_true")
    parser.add_argument(
        "--model",
        action="store_true",
        help="Выполнить настоящий вызов модели; при отсутствии ответа код 1",
    )
    parser.add_argument("--api-url", help="Например http://127.0.0.1:8000 внутри API-контейнера")
    args = parser.parse_args()
    failed = False
    if not args.registry or not args.cache:
        parser.error("Укажите --registry и --cache либо AI_REGISTRY_PATH и AI_CLINICAL_CACHE_DIR")
    if args.manifest:
        entries = json.loads(args.manifest.read_text(encoding="utf-8"))
        for entry in entries:
            path = (args.manifest.parent / entry["path"]).resolve()
            if not path.is_relative_to(args.manifest.parent.resolve()):
                raise ValueError("Неверный путь в manifest")
            with path.open("rb") as stream:
                valid = hashlib.file_digest(stream, "sha256").hexdigest() == entry["sha256"]
            if not valid or path.stat().st_size != entry["bytes"]:
                emit("manifest", status="FAILED", file=entry["path"])
                return 1
        emit("manifest", status="OK", files=len(entries))
    try:
        registry = RegistrySnapshot.load(args.registry)
        with NamedTemporaryFile(dir=args.cache, prefix=".write-check-", delete=True):
            pass
    except (OSError, ValueError) as error:
        emit("data_paths", status="FAILED", error_type=type(error).__name__)
        return 1
    resolved = RegistryIndex(registry).resolve("Сальбутамол", "100 мкг")
    emit(
        "registry",
        status="OK" if resolved.mnn else "NEEDS_REVIEW",
        version=registry.version,
        entries=len(registry.registrations),
        mnn=resolved.mnn,
        registrations=len(resolved.registrations),
    )
    try:
        candidates = recommendations_for_diagnosis(
            "J45.9", age_group="Взрослые", cache_dir=args.cache
        )
        emit(
            "selection",
            candidates=len(candidates),
            editions=[c.edition_id for c in candidates],
            cache_states=[c.cache_state for c in candidates],
        )
        if len(candidates) == 1:
            page = fetch_recommendation(candidates[0].page_url, cache_dir=args.cache)
            fragments = fragments_for_inn(page.recommendation, resolved.mnn or "")
            emit(
                "fragments",
                current=page.recommendation.is_current_ministry_copy,
                count=len(fragments),
                cache_state=page.cache_state,
            )
    except (OSError, ValueError, httpx.HTTPError) as error:
        emit("clinical_sources", status="FAILED", error_type=type(error).__name__)
        failed = True

    if args.network_sources:
        for url in ("https://grls.rosminzdrav.ru/GRLS.aspx", BASE_URL + "/"):
            try:
                with httpx.Client(timeout=15, follow_redirects=True) as client:
                    response = client.get(url)
                emit(
                    "source_access",
                    url=url,
                    http_status=response.status_code,
                    tls_verification=True,
                )
            except (OSError, httpx.HTTPError) as error:
                emit("source_access", url=url, status="FAILED", error_type=type(error).__name__)
                failed = True
        try:
            candidates = recommendations_for_diagnosis(
                "E11.9", age_group="Взрослые", cache_dir=args.cache
            )
            missing = next(
                (
                    c
                    for c in candidates
                    if not (
                        args.cache
                        / (hashlib.sha256(c.page_url.encode()).hexdigest()[:24] + ".html")
                    ).exists()
                ),
                None,
            )
            if missing is None:
                emit("cache_miss_download", status="NOT_TESTED", reason="No missing candidate")
            else:
                emit("cache_miss_download", status="STARTED", edition=missing.edition_id)
                page = fetch_recommendation(missing.page_url, cache_dir=args.cache)
                emit(
                    "cache_miss_download",
                    status="OK",
                    cache_state=page.cache_state,
                    current=page.recommendation.is_current_ministry_copy,
                )
        except (OSError, ValueError, httpx.HTTPError) as error:
            emit("cache_miss_download", status="FAILED", error_type=type(error).__name__)
            failed = True

    if args.api_url:
        try:
            with httpx.Client(timeout=660) as client:
                response = client.post(
                    args.api_url.rstrip("/") + "/applications",
                    files={
                        "file": (
                            "test-request.xlsx",
                            test_file(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    },
                    data={"age_group": "adult"},
                )
            response.raise_for_status()
            card = response.json()
            emit(
                "backend_http",
                http_status=response.status_code,
                status=card["application"]["status"],
                traffic_light=card["application"]["traffic_light"],
                mnn=[i["mnn"] for i in card["items"]],
                checks=[
                    dict(
                        category=c["category"],
                        review_reason=c["review_reason"],
                        reference_version=c["reference_version"],
                    )
                    for i in card["items"]
                    for c in i["checks"]
                ],
            )
            if card["application"]["status"] != "analyzed":
                failed = True
            checks = [check for item in card["items"] for check in item["checks"]]
            if any(
                check["review_reason"] in {"AI_UNAVAILABLE", "AI_TIMEOUT", "AI_INVALID_RESPONSE"}
                for check in checks
            ):
                failed = True
            if not card["items"] or not any(item["mnn"] for item in card["items"]):
                failed = True
            identifier = card["application"]["id"]
            with httpx.Client(timeout=30) as client:
                saved = client.get(args.api_url.rstrip("/") + f"/applications/{identifier}")
                saved.raise_for_status()
                emit("backend_saved_card", status="OK" if saved.json() == card else "FAILED")
                failed = failed or saved.json() != card
                report = client.get(
                    args.api_url.rstrip("/") + f"/applications/{identifier}/report.xlsx"
                )
                report.raise_for_status()
            from openpyxl import load_workbook

            workbook = load_workbook(io.BytesIO(report.content), read_only=True)
            emit(
                "backend_report",
                status="OK",
                sheets=workbook.sheetnames,
                item_rows=workbook["Препараты"].max_row - 1,
            )
            workbook.close()
        except (OSError, ValueError, httpx.HTTPError) as error:
            emit("backend_http", status="FAILED", error_type=type(error).__name__)
            return 1
    else:
        settings = AISettings(
            mode="network" if args.model else "template",
            corpus_path="",
            registry_path=str(args.registry),
            clinical_cache_dir=str(args.cache),
            live_sources=True,
            pair_timeout_seconds=180,
            application_timeout_seconds=600,
        )
        adapter = AIAdapter(settings)
        check = adapter.analyze(
            AIInput(
                drug_name="Сальбутамол", diagnosis="J45.9 Бронхиальная астма", age_group="adult"
            ),
            PairCheck(needs_review=True),
        )
        emit(
            "backend_worker",
            mnn=adapter.last_mnn,
            category=check.category,
            needs_review=check.needs_review,
            review_reason=check.review_reason,
            reference_version=check.reference_version,
            diagnostics=adapter.last_diagnostics,
            tokens=adapter.last_tokens,
            sent_fields=adapter.last_sent_fields,
        )
        failed = failed or not adapter.last_mnn
        if args.model and not adapter.last_diagnostics.get("model_responses"):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
