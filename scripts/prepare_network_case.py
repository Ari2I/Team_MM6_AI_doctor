"""Создать одну синтетическую XLSX по публичным ГРЛС/КР; запускается пользователем."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
DATA = Path("/var/lib/vrach/rag")


def _write_case(
    folder: Path, *, code: str, group: str, recommendation, entry, registry_version: str
) -> None:
    from openpyxl import Workbook

    diagnosis = f"{code} {' '.join(recommendation.title.split())}"
    workbook = Workbook()
    workbook.active.title = "Синтетическая проверка"
    workbook.active.append(["Торговое наименование", "Дозировка", "Количество", "Сумма", "Диагноз"])
    # Значения 1 и 1000 — явно заданные тестовые данные, не факты о лечении/цене.
    # Название и форма копируются из реестра без изменения концентрации.
    workbook.active.append([entry.trade_name, entry.form, 1, 1000, diagnosis])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    (folder / "control.xlsx").write_bytes(stream.getvalue())
    policy = dict(
        diagnoses=[diagnosis],
        inns=[entry.mnn],
        diagnosis_aliases=[dict(name=diagnosis, icd10=code, edition=recommendation.edition_id)],
    )
    (folder / "network-terms.json").write_text(
        json.dumps(policy, ensure_ascii=False), encoding="utf-8"
    )
    (folder / "case.json").write_text(
        json.dumps(
            dict(
                schema_version="network-case.1",
                synthetic=True,
                age_group=group,
                icd10=code,
                diagnosis=diagnosis,
                edition=recommendation.edition_id,
                inn=entry.mnn,
                registry_version=registry_version,
                recommendation_url=recommendation.primary_url,
                workbook_sha256=sha256(stream.getvalue()).hexdigest(),
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_case(folder: Path, **values) -> None:
    if folder.exists():
        raise ValueError("CASE_EXISTS")
    with TemporaryDirectory(prefix=".case-", dir=folder.parent) as temporary:
        staging = Path(temporary)
        _write_case(staging, **values)
        staging.rename(folder)


def prepare(code: str, group: str) -> None:
    if not re.fullmatch(r"[A-Z][0-9]{2}(?:\.[0-9]{1,4})?", code):
        raise ValueError
    folder = DATA / "acceptance"
    if folder.exists():
        raise ValueError("CASE_EXISTS")
    sys.path.insert(0, str(ROOT / "ai/src"))
    from llm_rag.ml2.clinicalrecs import fragments_for_inn
    from llm_rag.ml2.grls import build_registry_snapshot, download_latest
    from llm_rag.ml2.official import ClinicalSources, select_edition
    from llm_rag.ml2.registry import RegistryIndex, RegistrySnapshot, inn_key

    # Только подготовка публичных источников, не бюджет анализа заявки.
    deadline = monotonic() + 240
    sources = ClinicalSources(deadline)
    population = {"adult": "Взрослые", "child": "Дети"}[group]
    recommendation = select_edition(
        sources, list(sources.candidates(code, population)), code=code, age=population, edition=None
    ).recommendation
    archive, as_of, digest = download_latest(DATA / "cache/grls", deadline=deadline)
    path = DATA / "cache/grls" / f"registry-{digest}.json"
    if not path.exists():
        build_registry_snapshot(archive, as_of, path)
    registry = RegistrySnapshot.load(path)
    index = RegistryIndex(registry)
    unique = {}
    for entry in registry.registrations:
        if entry.mnn and "+" not in entry.mnn and entry.form:
            unique.setdefault(inn_key(entry.mnn), entry)
    # Никакая категория/медицинская применимость здесь не назначается.
    # Подбирается только контролируемое техническое совпадение публичного текста.
    public_text = " ".join(s.text.casefold().replace("ё", "е") for s in recommendation.segments)
    for key, entry in sorted(unique.items()):
        tokens = re.findall(r"[а-яa-z-]{4,}", key)
        if not tokens or not any(token in public_text for token in tokens):
            continue
        fragments = fragments_for_inn(recommendation, entry.mnn, max_fragments=8, window_chars=1600)
        if not fragments or any(len(f.text.encode()) > 3000 for f in fragments):
            continue
        if index.resolve(entry.trade_name, entry.form).mnn != entry.mnn:
            continue
        write_case(
            folder,
            code=code,
            group=group,
            recommendation=recommendation,
            entry=entry,
            registry_version=registry.version,
        )
        return
    raise ValueError("NO_CONTROL_PAIR")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--icd10", default="J45.9")
    parser.add_argument("--age-group", choices=["adult", "child"], default="adult")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    try:
        prepare(args.icd10, args.age_group)
    except Exception:
        print(
            "Заявка не подготовлена: проверьте доступ к источникам, "
            "единственную редакцию и отсутствие прежнего acceptance."
        )
        return 1
    print(
        "Синтетическая заявка и локальное соответствие МКБ/КР подготовлены; модель не вызывалась."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
