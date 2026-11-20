"""Compose без .env; необязательный CA bind создаётся только при заданном пути."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def ca_override(path: str, source: str | None = None) -> dict[str, object]:
    """Путь в environment остаётся ${VAR:-}; монтируем туда же, read-only."""
    return {
        "services": {
            "api": {
                "volumes": [
                    {
                        "type": "bind",
                        "source": source or path,
                        "target": path,
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    }
                ],
            }
        }
    }


def main() -> int:
    # Читается только путь CA, не ключ и не набор переменных окружения.
    ca_path = os.environ.get("GIGACHAT_CA_BUNDLE_FILE", "")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--ca-source")
    options, args = parser.parse_known_args()
    root = Path(__file__).resolve().parents[1]
    command = [
        "docker",
        "compose",
        "--env-file",
        os.devnull,
        "-f",
        str(root / "docker-compose.yml"),
    ]
    if not ca_path:
        if options.ca_source:
            parser.error("Задайте GIGACHAT_CA_BUNDLE_FILE — путь внутри контейнера.")
        return subprocess.call([*command, *args], cwd=root)
    # Docker Desktop допускает source C:/..., но target в Linux должен быть абсолютным.
    # Поэтому для Windows используйте отдельный Linux-путь внутри контейнера,
    # см. README: хостовый путь задаётся опцией --ca-source.
    source = options.ca_source or ca_path
    if not ca_path.startswith("/") or not Path(source).is_file():
        parser.error("Нужны существующий CA-файл хоста и абсолютный Linux-путь в контейнере.")
    override = ca_override(ca_path, source)
    with tempfile.TemporaryDirectory(prefix="vrach-compose-") as directory:
        target = Path(directory) / "ca.json"
        target.write_text(json.dumps(override), encoding="utf-8")
        return subprocess.call([*command, "-f", str(target), *args], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
