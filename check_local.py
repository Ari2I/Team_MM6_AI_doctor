"""Терминальное меню для ручной проверки существующей функции анализа."""

from datetime import datetime
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pydantic import ValidationError

from llm_rag.application import analyze_files
from llm_rag.embeddings import EmbeddingError
from llm_rag.providers.base import PROVIDER_ERROR_MESSAGES


MODES = {
    "1": ("demo", "lexical"), "2": ("demo", "vector"),
    "3": ("gigachat", "lexical"), "4": ("gigachat", "vector"),
}


def _input_path(prompt: str, current: Path) -> Path:
    value = input(f"{prompt} [{current}]: ").strip().strip('"')
    if not value:
        return current
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _show_result(result, output_path: Path) -> None:
    print(f"\nПровайдер: {result.provider_name}; поиск: {result.retrieval_info.method}")
    print(f"Полнота результата: {result.completeness}")
    for item in result.items:
        print(f"\n{item.item_id}: {item.explanation_status}\n{item.comment}")
        for reason in item.reasons:
            if reason in PROVIDER_ERROR_MESSAGES:
                print(f"  Причина: {reason} — {PROVIDER_ERROR_MESSAGES[reason]}")
        for claim in item.claims:
            for citation in claim.citations:
                print(f"  Источник: {citation.title}, {citation.location}")
                print(f"  Цитата: {citation.quote}")
    for cost in result.costs_by_currency:
        print(f"\nСумма заявки: {cost.total_amount} {cost.currency}")
    errors = sum(item.explanation_status == "PROCESSING_ERROR" for item in result.items)
    if errors:
        print(f"Технические ошибки у позиций: {errors}. Наличие файла не означает успешную работу API.")
        print("Для GigaChat проверьте ключ, scope, доступ к модели и сетевое соединение.")
        if any("GIGACHAT_TLS_ERROR" in item.reasons for item in result.items):
            print("Для локальной проверки без сертификата можно отключить его проверку пунктом 7.")
    print(f"Полный результат сохранён: {output_path}")


def main() -> int:
    """Выбрать режим и вызвать ту же функцию, которую использует run.py."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    request_path = PROJECT_ROOT / "examples/request.json"
    corpus_path = PROJECT_ROOT / "examples/corpus.json"
    allow_download = False
    verify_ssl_certs = True
    try:
        while True:
            print("\nЛокальная проверка LLM/RAG — демонстрационные данные")
            print(f"Заявка: {request_path}\nКорпус: {corpus_path}")
            print("1 — Демо без API, обычный поиск")
            print("2 — Демо без API, векторный поиск")
            print("3 — GigaChat, обычный поиск: отправляет данные в API")
            print("4 — GigaChat, векторный поиск: отправляет данные в API")
            print("5 — Выбрать файлы заявки и корпуса")
            print(f"6 — Разрешить скачивание модели: {'да' if allow_download else 'нет'}")
            print(f"7 — Проверка сертификата: {'включена' if verify_ssl_certs else 'выключена'}")
            print("0 — Выход")
            choice = input("Выберите пункт: ").strip()
            if choice == "0":
                return 0
            if choice == "5":
                request_path = _input_path("Файл заявки", request_path)
                corpus_path = _input_path("Файл корпуса", corpus_path)
                continue
            if choice == "6":
                allow_download = not allow_download
                continue
            if choice == "7":
                verify_ssl_certs = not verify_ssl_certs
                if not verify_ssl_certs:
                    print("Проверка подлинности сервера отключена для текущего меню. "
                          "Соединение зашифровано, но подлинность сервера не проверяется.")
                continue
            if choice not in MODES:
                print("Неизвестный пункт. Введите число от 0 до 7.")
                continue
            provider, retrieval = MODES[choice]
            print("Обработка началась. Дождитесь результата…", flush=True)
            try:
                result = analyze_files(
                    request_path, corpus_path, provider_name=provider, retrieval=retrieval,
                    enable_api=provider == "gigachat", timeout=60.0,
                    download_embeddings=allow_download and retrieval == "vector",
                    env_file=PROJECT_ROOT / ".env",
                    verify_ssl_certs=verify_ssl_certs,
                )
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                output_path = PROJECT_ROOT / ".local/results" / f"check-{provider}-{retrieval}-{timestamp}.json"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
                _show_result(result, output_path)
            except EmbeddingError as error:
                print(f"Ошибка эмбеддингов: {error}")
                print("Если весов ещё нет, включите скачивание пунктом 6 и повторите проверку.")
            except ValidationError:
                print("Ошибка структуры данных: проверьте заявку и корпус по примерам.")
            except (OSError, ValueError, UnicodeError):
                print("Ошибка файлов или настройки. Проверьте пути и .env: для GigaChat нужен "
                      "GIGACHAT_CREDENTIALS либо GIGACHAT_ACCESS_TOKEN, но не оба сразу.")
    except (EOFError, KeyboardInterrupt):
        print("\nЛокальная проверка завершена.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
