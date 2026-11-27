"""Создать одну синтетическую XLSX по публичным ГРЛС/КР; запускается пользователем."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.network_case_checks import full_inn_pattern, normalize_public_text  # noqa: E402

DATA = Path("/var/lib/vrach/rag")


def _write_case(
    folder: Path,
    *,
    code: str,
    group: str,
    recommendation: Any,
    entry: Any,
    registry_version: str,
    control_kind: Literal["positive", "negative"] = "positive",
    negative_entry: Any | None = None,
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
                control_kind=control_kind,
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

    if negative_entry is not None:
        negative_folder = folder / "negative"
        negative_folder.mkdir()
        _write_case(
            negative_folder,
            code=code,
            group=group,
            recommendation=recommendation,
            entry=negative_entry,
            registry_version=registry_version,
            control_kind="negative",
        )
        policy["inns"].append(negative_entry.mnn)
        (folder / "network-terms.json").write_text(
            json.dumps(policy, ensure_ascii=False), encoding="utf-8"
        )


def write_case(folder: Path, **values) -> None:
    if folder.exists():
        raise ValueError("CASE_EXISTS")
    with TemporaryDirectory(prefix=".case-", dir=folder.parent) as temporary:
        staging = Path(temporary)
        _write_case(staging, **values)
        staging.rename(folder)


def select_control_entry(
    registrations: Iterable[Any],
    *,
    recommendation: Any,
    index: Any,
    load_fragments: Callable[..., Sequence[Any]],
    negative: bool = False,
) -> Any:
    """Выбрать реестровую запись с полным МНН во всех предлагаемых фрагментах.

    Для отрицательного случая не должно быть ни полного МНН, ни фрагментов
    библиотечного поиска. Это технический контроль, без вывода о клинической
    применимости. Проверка не вызывает сеть и не импортирует AI.
    """
    unique = {}
    for entry in registrations:
        if entry.mnn and "+" not in entry.mnn and entry.form:
            unique.setdefault(normalize_public_text(entry.mnn), entry)
    segments = tuple(normalize_public_text(s.text) for s in recommendation.segments)
    for entry in (unique[key] for key in sorted(unique)):
        pattern = full_inn_pattern(entry.mnn)
        if pattern is None:
            continue
        mentioned = any(pattern.search(text) for text in segments)
        if (negative and mentioned) or (not negative and not mentioned):
            continue
        fragments = load_fragments(recommendation, entry.mnn, max_fragments=8, window_chars=1600)
        if negative and fragments:
            continue
        if not negative and (
            not fragments
            or any(
                len(fragment.text.encode("utf-8")) > 3000
                or not pattern.search(normalize_public_text(fragment.text))
                for fragment in fragments
            )
        ):
            continue
        if index.resolve(entry.trade_name, entry.form).mnn == entry.mnn:
            return entry
    raise ValueError("NO_CONTROL_PAIR")


def prepare(
    code: str,
    group: str,
    *,
    case_dir: Path | None = None,
    with_negative: bool = False,
) -> None:
    if not re.fullmatch(r"[A-Z][0-9]{2}(?:\.[0-9]{1,4})?", code):
        raise ValueError
    folder = case_dir if case_dir is not None else DATA / "acceptance"
    if folder.exists():
        raise ValueError("CASE_EXISTS")
    sys.path.insert(0, str(ROOT / "ai/src"))
    from llm_rag.ml2.clinicalrecs import fragments_for_inn
    from llm_rag.ml2.grls import build_registry_snapshot, download_latest
    from llm_rag.ml2.official import ClinicalSources, select_edition
    from llm_rag.ml2.registry import RegistryIndex, RegistrySnapshot

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
    entry = select_control_entry(
        registry.registrations,
        recommendation=recommendation,
        index=index,
        load_fragments=fragments_for_inn,
    )
    negative_entry = None
    if with_negative:
        negative_entry = select_control_entry(
            registry.registrations,
            recommendation=recommendation,
            index=index,
            load_fragments=fragments_for_inn,
            negative=True,
        )
    write_case(
        folder,
        code=code,
        group=group,
        recommendation=recommendation,
        entry=entry,
        registry_version=registry.version,
        negative_entry=negative_entry,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--icd10", default="J45.9")
    parser.add_argument("--age-group", choices=["adult", "child"], default="adult")
    parser.add_argument("--case-dir", type=Path)
    parser.add_argument("--with-negative", action="store_true")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    try:
        prepare(
            args.icd10,
            args.age_group,
            case_dir=args.case_dir,
            with_negative=args.with_negative,
        )
    except Exception:
        print(
            "Заявка не подготовлена: проверьте доступ к источникам, "
            "единственную редакцию, пары контроля и отсутствие выбранного каталога."
        )
        return 1
    print(
        "Синтетическая заявка и локальное соответствие МКБ/КР подготовлены; модель не вызывалась."
    )
    if args.with_negative:
        print("Отдельный отрицательный случай подготовлен; в выбранной КР фрагменты не найдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
