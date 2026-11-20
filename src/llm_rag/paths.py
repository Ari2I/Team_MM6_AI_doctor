"""Ресурсы исходников и установленного пакета без зависимости от cwd."""

from pathlib import Path
import sysconfig


def source_root():
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() and (root / "src/llm_rag").is_dir() else None


def example_path(name):
    root = source_root()
    folder = root / "examples" if root else Path(sysconfig.get_path("data")) / "share/llm-rag/examples"
    path = folder / name
    if not path.is_file():
        raise ValueError("Примеры отсутствуют в установке; укажите вход и корпус явно.")
    return path
