"""Сравнить сохранённые снимки с работающим стеком или другим каталогом."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.golden_support import (  # noqa: E402
    CASES,
    ROOT,
    GoldenError,
    capture_from_stack,
    differences,
    normalize,
    read_cards,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-dir", type=Path, default=ROOT / "tests/golden")
    parser.add_argument("--current-dir", type=Path, help="Сравнить локальные снимки без сети")
    args = parser.parse_args(argv)
    try:
        baseline = read_cards(args.golden_dir)
        current = read_cards(args.current_dir) if args.current_dir else capture_from_stack()
        changed = False
        for case in CASES:
            changes = differences(normalize(baseline[case]), normalize(current[case]))
            if changes:
                changed = True
                print(f"{case}: обнаружены различия")
                for change in changes[:100]:
                    print(f"  {change}")
                if len(changes) > 100:
                    print("  Остальные различия не показаны.")
    except GoldenError as error:
        print(str(error))
        return 2
    except Exception:
        print("Не удалось сравнить снимки; проверьте файлы, доступ и ответ API.")
        return 2
    if not changed:
        print("Все 4 снимка совпадают.")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
