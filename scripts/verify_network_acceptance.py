"""Проверить сохранённый синтетический результат без печати заявки и SDK-ответов."""

from __future__ import annotations

import argparse
import json
import logging
import math
import socket
import sys
from hashlib import sha256
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.network_case_checks import full_inn_pattern, normalize_public_text  # noqa: E402

DATA = Path("/var/lib/vrach/rag")
FOLDER = DATA / "acceptance"
INDEX = DATA / "indexes/official-embeddings.json"


def hash_file(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def files_manifest() -> dict[str, str]:
    registries = list((DATA / "cache/grls").glob("*.json"))
    archives = list((DATA / "cache/grls").glob("*.zip"))
    files = registries + archives
    files.append(INDEX)
    if not INDEX.is_file() or not registries or not archives:
        raise ValueError("NO_RESOURCES")
    return {str(path.relative_to(DATA)): hash_file(path) for path in files}


def baseline(folder: Path | None = None) -> None:
    target = (folder if folder is not None else FOLDER) / "embedding-before.json"
    if target.exists():
        raise ValueError("BASELINE_EXISTS")
    value = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    target.write_text(json.dumps(sorted(value.get("vectors", {}))), encoding="utf-8")
    print("Исходные хэши векторов зафиксированы; индекс не изменён.")


def _model_pair(result: dict) -> dict:
    """Общие требования к полной обработке и модельному разбору одной пары."""
    if result["completeness"] != "COMPLETE" or result["unprocessed_pairs"]:
        raise ValueError("PARTIAL_RESULT")
    if len(result["pairs"]) != 1:
        raise ValueError("PAIR_COUNT")
    pair = result["pairs"][0]
    forbidden = {
        "AI_TIMEOUT",
        "AI_UNAVAILABLE",
        "AI_INVALID_RESPONSE",
        "SOURCE_ERROR",
        "DIAGNOSIS_MISMATCH",
        "NETWORK_POLICY_DENIED",
        "BUDGET_EXHAUSTED",
    }
    if forbidden.intersection(pair["reasons"]):
        raise ValueError("PAIR_FAILURE")
    if pair["generation_mode"] != "MODEL" or pair.get("staff_analysis") is None:
        raise ValueError("NO_MODEL_REPORT")
    if result.get("usage", {}).get("calls", 0) == 0:
        raise ValueError("NO_MODEL_CALLS")
    if not result["manifest"].get("registry") or not result["manifest"].get("clinical"):
        raise ValueError("NO_RESOURCE_VERSIONS")
    return pair


def assess(result: dict, *, new_vectors: set[str]) -> None:
    pair = _model_pair(result)
    if not pair["citations"] or not (result.get("evidence_snapshot") or {}).get("documents"):
        raise ValueError("NO_SAVED_EVIDENCE")
    # Критерий относится только к одной синтетической контрольной паре.
    # Ссылки и наличие цитаты сами по себе не связывают её с проверяемым МНН.
    pattern = full_inn_pattern(pair.get("inn"))
    if pattern is None or any(
        normalize_public_text(document["inn"]) != normalize_public_text(pair["inn"])
        or not pattern.search(normalize_public_text(document["text"]))
        for document in result["evidence_snapshot"]["documents"]
    ):
        raise ValueError("UNRELATED_CONTROL_EVIDENCE")
    if any(
        not pattern.search(normalize_public_text(citation["quote"]))
        for citation in pair["citations"]
    ):
        raise ValueError("UNRELATED_CONTROL_CITATION")
    info = result.get("retrieval_info") or {}
    if (
        info.get("method") != "vector"
        or not info.get("embedding_revision")
        or info["embedding_revision"].startswith("unverified:")
        or not new_vectors
    ):
        raise ValueError("NO_FRESH_EMBEDDINGS")
    expected_keys = set()
    for document in result["evidence_snapshot"]["documents"]:
        prefix = f"МНН: {document['inn']}. Диагноз: {document['diagnosis']}."
        expected_keys.add(sha256(prefix.encode()).hexdigest())
        expected_keys.add(sha256((prefix + " " + document["text"]).encode()).hexdigest())
    if not new_vectors.intersection(expected_keys):
        raise ValueError("NO_FRESH_CONTROL_VECTORS")


def assess_negative(result: dict) -> None:
    """Нет фрагментов: не создавать категорию или цитаты; модель объясняет ограничение."""
    pair = _model_pair(result)
    decision = pair["decision"]
    if (
        not pair.get("inn")
        or "NO_EVIDENCE" not in pair["reasons"]
        or decision["classification_basis"] != "NONE"
        or decision.get("upstream_rule_category") is not None
        or decision.get("proposed_category") is not None
        or pair["citations"]
        or (result.get("evidence_snapshot") or {}).get("documents")
    ):
        raise ValueError("NEGATIVE_CONTROL_FAILED")
    rows = result["human_report"]["items"]
    if len(rows) != 1 or rows[0]["sources"]:
        raise ValueError("NEGATIVE_CONTROL_FAILED")


def verify_index(result: dict, index: dict, fresh: set[str]) -> None:
    """Проверить индекс положительного контроля без изменения файла."""
    if index["revision"] != result["retrieval_info"]["embedding_revision"]:
        raise ValueError("INDEX_VERSION")
    dimension = result["retrieval_info"]["embedding_dimension"]
    if (
        index["dimension"] != dimension
        or index["model"] != result["retrieval_info"]["embedding_model"]
    ):
        raise ValueError("INDEX_MODEL")
    for key in fresh:
        vector = index["vectors"][key]
        if (
            len(vector) != dimension
            or not any(vector)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
        ):
            raise ValueError("INDEX_VECTORS")


def verify(identifier: UUID, folder: Path | None = None) -> None:
    from app.config import AISettings
    from app.db.session import SessionLocal
    from app.models import ApplicationAIResult, ApplicationItem, ItemCheck
    from app.models.enums import ReviewReason
    from app.services.ai_reports import read_ai_report

    folder = folder if folder is not None else FOLDER
    with SessionLocal() as session:
        stored = session.get(ApplicationAIResult, identifier)
        if stored is None:
            from sqlalchemy import select

            reasons = session.scalars(
                select(ItemCheck.review_reason)
                .join(ApplicationItem, ItemCheck.item_id == ApplicationItem.id)
                .where(ApplicationItem.application_id == identifier)
            )
            allowed = {reason.value for reason in ReviewReason}
            print(
                "Причины backend:", ",".join(sorted({r for r in reasons if r in allowed})) or "нет"
            )
            raise ValueError("REPORT_NOT_SAVED")
        result = stored.result
        report = read_ai_report(stored)
        settings = AISettings()
        if (
            stored.backend_revision != settings.backend_revision
            or stored.ai_revision != settings.library_revision
        ):
            raise ValueError("BUILD_MISMATCH")
        case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
        kind = case.get("control_kind", "positive")
        if kind not in {"positive", "negative"}:
            raise ValueError("INVALID_CONTROL_KIND")
        if len(result["request"]["purchases"]) != 1 or len(result["request"]["diagnoses"]) != 1:
            raise ValueError("NOT_CONTROL_CASE")
        diagnosis = result["request"]["diagnoses"][0]
        if (
            diagnosis["name"] != case["diagnosis"]
            or diagnosis["conditions"]["age_group"] != case["age_group"]
            or diagnosis["icd10"] != case["icd10"]
        ):
            raise ValueError("CASE_MISMATCH")
        # Печатаются только перечислимые коды, никаких текстов цитат/диагнозов.
        from app.services.integration_protocol import validate_result

        sys.path.insert(0, str(ROOT / "ai/src"))
        result = validate_result(result, result["request"])
        print("Статус сохранённого результата:", result["completeness"])
        print(
            "Режимы генерации:", ",".join(sorted({p["generation_mode"] for p in result["pairs"]}))
        )
        from llm_rag.integration_contracts import IntegrationResult

        IntegrationResult.model_validate(result)
        print(
            "Коды пар:",
            ",".join(sorted({r for p in result["pairs"] for r in p["reasons"]})) or "нет",
        )
        if kind == "positive":
            before = set(json.loads((folder / "embedding-before.json").read_text(encoding="utf-8")))
            index = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.is_file() else {}
            fresh = set(index.get("vectors", {})) - before
            assess(result, new_vectors=fresh)
            print("Полное МНН в фрагментах и итоговых цитатах: OK.")
            verify_index(result, index, fresh)
        else:
            assess_negative(result)
        if (
            result["pairs"][0]["inn"] != case["inn"]
            or result["manifest"]["registry"]["version"] != case["registry_version"]
            or kind == "positive"
            and not any(
                c["url"] == case["recommendation_url"] for c in result["pairs"][0]["citations"]
            )
        ):
            raise ValueError("SOURCES_CHANGED")
        if report.human_report.model_dump(mode="json") != result["human_report"]:
            raise ValueError("REPORT_MISMATCH")
        # Положительный TCP-контроль до изоляции, без HTTP, ключей и тела заявки.
        with socket.create_connection(("api.giga.chat", 443), timeout=5) as connection:
            address = connection.getpeername()[0]
        receipt = dict(
            files=files_manifest(),
            application_id=str(identifier),
            result_sha256=sha256(
                json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
            control_ip=address,
            control_kind=kind,
        )
        (folder / "resources-after.json").write_text(
            json.dumps(receipt, sort_keys=True), encoding="utf-8"
        )
        if kind == "positive":
            print("Модельный отчёт, новые векторы Embeddings, сохранённые цитаты и SHA сборки: OK.")
        else:
            print(
                "Отрицательный контроль: МНН определено, NO_EVIDENCE, "
                "категория и цитаты отсутствуют; MODEL: OK."
            )


def check_persistence(folder: Path | None = None) -> None:
    folder = folder if folder is not None else FOLDER
    before = json.loads((folder / "resources-after.json").read_text(encoding="utf-8"))
    if before["files"] != files_manifest():
        raise ValueError("RESOURCES_CHANGED")
    from app.db.session import SessionLocal
    from app.models import ApplicationAIResult

    with SessionLocal() as session:
        stored = session.get(ApplicationAIResult, UUID(before["application_id"]))
        if (
            stored is None
            or sha256(
                json.dumps(stored.result, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            != before["result_sha256"]
        ):
            raise ValueError("SAVED_RESULT_CHANGED")
    print("ГРЛС и векторный индекс после пересоздания не изменились: OK.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--baseline", action="store_true")
    actions.add_argument("--application-id", type=UUID)
    actions.add_argument("--state-stdin", action="store_true")
    actions.add_argument("--persistence", action="store_true")
    actions.add_argument("--check-egress-blocked", action="store_true")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    try:
        if args.baseline:
            baseline(args.case_dir)
        elif args.persistence:
            check_persistence(args.case_dir)
        elif args.check_egress_blocked:
            # Тот же публичный адрес, доступность которого проверена до изоляции.
            folder = args.case_dir if args.case_dir is not None else FOLDER
            receipt = json.loads((folder / "resources-after.json").read_text(encoding="utf-8"))
            try:
                with socket.create_connection((receipt["control_ip"], 443), timeout=3):
                    raise ValueError("EGRESS_NOT_BLOCKED")
            except OSError:
                print("Контрольное внешнее соединение недоступно.")
        else:
            identifier = (
                UUID(json.load(sys.stdin)["application_id"])
                if args.state_stdin
                else args.application_id
            )
            verify(identifier, args.case_dir)
    except Exception:
        print(
            "Приёмка не подтверждена: проверьте сохранённый результат, исходные векторы и ресурсы."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
