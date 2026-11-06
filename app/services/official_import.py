"""Офлайн-импорт зафиксированных официальных источников в один снимок."""

import argparse
import csv
from datetime import date
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.services.official_reference import CompositionEntry, EvidenceEntry, OfficialSnapshot, Source, import_grls

SOURCE_FIELDS = {
    "title", "url", "version", "location", "retrieved_at", "quote",
}


def read_reviewed_table(path: Path | None, model):
    if path is None:
        return []
    records = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = set(reader.fieldnames or ())
        if not SOURCE_FIELDS <= fields:
            raise ValueError(f"{path.name}: отсутствуют столбцы происхождения данных.")
        for row in reader:
            if not any(row.values()):
                continue
            try:
                source = Source(**{key: row.pop(key) for key in SOURCE_FIELDS})
                row = {key: value for key, value in row.items() if value}
                records.append(model(**row, source=source))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path.name}, строка {reader.line_num}: {exc}") from exc
    return records


def build_snapshot(registry: Path, as_of: date, *, registry_url: str,
                   evidence: Path | None = None, compositions: Path | None = None,
                   coverage="registry_export", assume_active=False) -> OfficialSnapshot:
    snapshot = OfficialSnapshot(
        version="pending", as_of=as_of, coverage=coverage,
        registrations=import_grls(registry, as_of=as_of, registry_url=registry_url,
                                  assume_active=assume_active),
        evidence=read_reviewed_table(evidence, EvidenceEntry),
        compositions=read_reviewed_table(compositions, CompositionEntry),
    )
    digest = sha256(snapshot.model_dump_json().encode()).hexdigest()[:16]
    return snapshot.model_copy(update={"version": f"official-{as_of}-{digest}"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grls", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--registry-url", default="https://grls.minzdrav.gov.ru/grls.aspx")
    parser.add_argument("--evidence", type=Path, help="Проверенные выдержки из стандартов, КР и ОХЛП.")
    parser.add_argument("--compositions", type=Path, help="Проверенные выдержки из раздела 2 ОХЛП.")
    parser.add_argument("--coverage", choices=("registry_export", "limited", "historical"),
                        default="registry_export")
    parser.add_argument("--assume-active", action="store_true",
                        help="Для отдельного XLSX: файл официально содержит только действующие РУ.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(args.output.resolve() == p.resolve() for p in (args.grls, args.evidence, args.compositions) if p):
        parser.error("Путь результата совпадает с входным файлом.")
    try:
        snapshot = build_snapshot(
            args.grls, args.as_of, registry_url=args.registry_url, evidence=args.evidence,
            compositions=args.compositions, coverage=args.coverage, assume_active=args.assume_active,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=args.output.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(snapshot.model_dump_json().encode())
            temporary.replace(args.output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Импорт не выполнен. {exc}\n")
    print(f"{snapshot.version} | регистрационные записи {len(snapshot.registrations)} | "
          f"связи {len(snapshot.evidence)} | составы {len(snapshot.compositions)}")


if __name__ == "__main__":
    main()
