"""Проверка network-сборки без запросов, чтения ключа и создания провайдеров."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
import pwd
import ssl
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path("/var/lib/vrach/rag")
PROBE = ".network-build-probe"
PROBE_CONTENT = "vrach-network-build.1\n"
NETWORK_EVENTS = {"socket.__new__", "socket.connect", "socket.getaddrinfo", "socket.sendto"}


class BuildCheckError(RuntimeError):
    """Только фиксированный код, без данных окружения или стороннего исключения."""


def prepare_volume(data: Path) -> None:
    """Разрешить appuser запись только в корень отдельного смонтированного тома."""
    if os.geteuid() != 0 or not data.is_mount() or data.resolve() != data:
        raise BuildCheckError("VOLUME_PREPARE")
    account = pwd.getpwnam("appuser")
    os.chown(data, account.pw_uid, account.pw_gid)


def check_settings() -> None:
    from app.config import AISettings

    settings = AISettings()
    if not settings.backend_revision or not settings.library_revision:
        raise BuildCheckError("BUILD_REVISIONS")
    if os.environ.get("GIGACHAT_SCOPE") not in {"GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}:
        raise BuildCheckError("SCOPE")
    if os.environ.get("RAG_DATA_DIR") != str(DATA_DIR):
        raise BuildCheckError("DATA_PATH")


def check_dependencies() -> None:
    if sys.version_info[:2] != (3, 11):
        raise BuildCheckError("PYTHON_VERSION")
    if importlib.metadata.version("gigachat") != "0.2.0":
        raise BuildCheckError("SDK_VERSION")
    source = ROOT / "ai/src"
    if source.is_dir() and str(source) not in sys.path:
        sys.path.insert(0, str(source))
    for name in ("gigachat", "httpx", "jsonschema", "llm_rag.integration", "llm_rag.runtime"):
        importlib.import_module(name)
    from llm_rag.rag_data import default_data_dir

    if default_data_dir() != DATA_DIR.resolve():
        raise BuildCheckError("LIBRARY_DATA_PATH")


def check_ca() -> int:
    ca = os.environ.get("GIGACHAT_CA_BUNDLE_FILE", "")
    if not ca or not Path(ca).is_absolute():
        raise BuildCheckError("CA_PATH")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=ca)
    count = context.cert_store_stats()["x509_ca"]
    if count == 0:
        raise BuildCheckError("CA_EMPTY")
    return count


def check_storage(data: Path, *, check_probe: bool) -> None:
    if os.geteuid() == 0:
        raise BuildCheckError("API_USER")
    if not data.is_mount() or data.resolve() != data:
        raise BuildCheckError("DATA_MOUNT")
    for relative in ("cache/grls", "indexes"):
        directory = data / relative
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as stream:
            stream.write(b"network-build-check\n")
            stream.flush()
    probe = data / PROBE
    if check_probe:
        if probe.read_text(encoding="utf-8") != PROBE_CONTENT:
            raise BuildCheckError("PROBE_CHANGED")
    else:
        probe.write_text(PROBE_CONTENT, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--prepare-volume", action="store_true")
    actions.add_argument("--write-probe", action="store_true")
    actions.add_argument("--check-probe", action="store_true")
    args = parser.parse_args(argv)
    attempts: list[str] = []

    def deny_network(event: str, values: tuple[object, ...]) -> None:
        if event in NETWORK_EVENTS:
            attempts.append(event)
            raise PermissionError("Сеть запрещена при подготовке сборки.")

    sys.addaudithook(deny_network)
    try:
        if args.prepare_volume:
            prepare_volume(DATA_DIR)
            print("Корень RAG-тома подготовлен для appuser.")
        else:
            check_settings()
            check_dependencies()
            count = check_ca()
            check_storage(DATA_DIR, check_probe=args.check_probe)
            if attempts:
                raise BuildCheckError("NETWORK_ATTEMPT")
            print("SHA, scope, SDK и импорты AI: OK.")
            print(f"CA загружен; сертификатов CA: {count}.")
            print("Запись в каталоги ГРЛС/индекса и контрольный файл: OK.")
    except BuildCheckError as error:
        print(f"Проверка сборки не пройдена: {error.args[0]}.")
        return 1
    except Exception:
        print("Проверка сборки не пройдена: DEPENDENCIES_CA_OR_STORAGE.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
