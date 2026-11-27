"""Сохранить карточки четырёх синтетических файлов из работающего стека."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.golden_support import CASES, ROOT, GoldenError, capture_from_stack  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tests/golden")
    parser.add_argument("--overwrite", action="store_true", help="Явно заменить прежние снимки")
    args = parser.parse_args(argv)
    try:
        if not args.overwrite and any(
            (args.output_dir / f"{case}.json").exists() for case in CASES
        ):
            raise GoldenError("Снимки уже существуют; выберите другой каталог или --overwrite.")
        cards = capture_from_stack()
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for case, card in cards.items():
            with (args.output_dir / f"{case}.json").open(
                "w" if args.overwrite else "x", encoding="utf-8"
            ) as stream:
                stream.write(json.dumps(card, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    except GoldenError as error:
        print(str(error))
        return 2
    except Exception:
        print("Не удалось сохранить снимки; проверьте доступ, каталог и ответ API.")
        return 2
    print("Сохранены 4 нормализованных снимка синтетического демо.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
