"""Запуск из папки с исходным кодом без установки пакета в редактируемом режиме."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from llm_rag.__main__ import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
