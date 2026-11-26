"""Локальная проверка XLSX через точку входа бэкенда."""

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import httpx
from llm_rag.integration import analyze_request
from llm_rag.integration_excel import read_integration_card, write_integration_report


def check_card(source: Path, output: Path) -> int:
    try:
        request = read_integration_card(source)
    except (ValueError, OSError) as error:
        print(f"{source.name}: ошибка чтения XLSX ({type(error).__name__}).", file=sys.stderr)
        return 2
    try:
        result = analyze_request(request, verify_ssl_certs=False)
        write_integration_report(result, output)
    except (ValueError, OSError, httpx.HTTPError) as error:
        print(
            f"{source.name}: ошибка проверки ({type(error).__name__}). "
            "Проверьте XLSX, доступ к источникам и настройки.",
            file=sys.stderr,
        )
        return 2
    print(output.resolve() / "report.html")
    failures = {
        "SOURCE_ERROR",
        "AI_UNAVAILABLE",
        "AI_INVALID_RESPONSE",
        "AI_TIMEOUT",
        "INTERNAL_ERROR",
        "NETWORK_POLICY_DENIED",
        "BUDGET_EXHAUSTED",
        "CONTEXT_LIMIT",
    }
    return int(
        result.completeness != "COMPLETE"
        or result.lifecycle_status == "CLOSE_FAILED"
        or any(
            failures.intersection(pair.reasons) or pair.generation_mode == "FALLBACK" for pair in result.pairs
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input", nargs="?", type=Path, help="Одна XLSX. По умолчанию все XLSX из excel_input."
    )
    parser.add_argument("--output", type=Path, help="Каталог результатов. По умолчанию excel_results.")
    args = parser.parse_args(argv)
    run_name = datetime.now().strftime("check-%Y%m%d-%H%M%S-%f")
    if args.input is not None:
        return check_card(args.input, args.output or ROOT / "excel_results" / run_name)

    inputs = ROOT / "excel_input"
    try:
        sources = sorted(
            path
            for path in inputs.iterdir()
            if path.is_file() and path.suffix.lower() == ".xlsx" and not path.name.startswith("~$")
        )
    except OSError:
        print(f"Не удалось прочитать папку {inputs}", file=sys.stderr)
        return 2
    if not sources:
        print(f"В папке {inputs} нет файлов XLSX.", file=sys.stderr)
        return 2

    run = (args.output or ROOT / "excel_results") / run_name
    status = 0
    for source in sources:
        status = max(status, check_card(source, run / source.name))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
