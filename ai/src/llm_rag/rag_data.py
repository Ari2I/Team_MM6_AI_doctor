"""Версионированная локальная поставка разрешённых данных с атомарным указателем."""

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator
from llm_rag.contracts import Model, Text


def default_data_dir():
    """Корень исходников при наличии pyproject; установленный пакет использует каталог данных ОС."""
    if os.environ.get("RAG_DATA_DIR"):
        return Path(os.environ["RAG_DATA_DIR"]).expanduser().resolve()
    project = Path(__file__).resolve().parents[2]
    if (project / "pyproject.toml").is_file():
        return project / "data/rag"
    base = Path(
        os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
    )
    return base / "llm-rag/rag"


class DataResource(Model):
    resource_id: Text
    kind: Literal["registry", "clinical", "corpus", "policy", "index"]
    version: Text
    path: Text
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    source_url: Text
    copy_url: Text | None = None
    retrieved_at: Text
    rights_status: Literal["APPROVED", "PENDING", "RESTRICTED"]
    rights_basis: Text

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or ":" in value
            or "\\" in value
            or value in {".", "", "manifest.json"}
        ):
            raise ValueError("Нужен относительный путь внутри поставки.")
        return value

    @field_validator("source_url", "copy_url")
    @classmethod
    def public_url(cls, value):
        from urllib.parse import urlsplit

        if value is not None:
            url = urlsplit(value)
            if url.scheme not in {"https", "http"} or not url.netloc or url.username or url.password:
                raise ValueError("Нужен публичный URL без учётных данных.")
        return value

    @field_validator("retrieved_at")
    @classmethod
    def timestamp(cls, value):
        from datetime import datetime

        if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("Дата получения должна включать часовой пояс.")
        return value


class DataManifest(Model):
    schema_version: Literal["rag-data.1"]
    version: Text
    resources: list[DataResource]

    @model_validator(mode="after")
    def unique_resources(self):
        for values in ([r.resource_id for r in self.resources], [r.path for r in self.resources]):
            if len(values) != len(set(values)):
                raise ValueError("Идентификаторы и пути ресурсов должны быть уникальны.")
        return self


def _inside(root, path):
    root = Path(root).resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Путь вышел за пределы набора.")
    return resolved


def _hash(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(manifest, folder):
    if not manifest.resources:
        raise ValueError("Набор данных пуст; нужны разрешённые ресурсы.")
    for resource in manifest.resources:
        if resource.rights_status != "APPROVED":
            raise ValueError("Право использования/распространения ресурса не подтверждено.")
        if _hash(_inside(folder, resource.path)) != resource.sha256:
            raise ValueError("SHA ресурса не совпадает с manifest.")


@dataclass(frozen=True)
class InstalledData:
    manifest: DataManifest
    folder: Path

    def resource_path(self, resource_id):
        resource = next((r for r in self.manifest.resources if r.resource_id == resource_id), None)
        if resource is None:
            raise ValueError("Ресурс отсутствует в текущем наборе.")
        path = _inside(self.folder, resource.path)
        if _hash(path) != resource.sha256:
            raise ValueError("Ресурс изменился после проверки набора.")
        return path


def load_data(root=None):
    root = Path(root) if root is not None else default_data_dir()
    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    folder = _inside(root, pointer["path"])
    raw = (folder / "manifest.json").read_bytes()
    if sha256(raw).hexdigest() != pointer["manifest_sha256"]:
        raise ValueError("Manifest активного набора изменён.")
    manifest = DataManifest.model_validate_json(raw)
    _verify(manifest, folder)
    return InstalledData(manifest, folder)


def prepare_data(manifest, source_dir, root=None):
    """Проверить весь набор до копирования. Не скачивает данные, не включает сеть."""
    manifest = DataManifest.model_validate(manifest)
    root = Path(root) if root is not None else default_data_dir()
    _verify(manifest, source_dir)
    raw = manifest.model_dump_json(indent=2).encode()
    digest = sha256(raw).hexdigest()
    destination = _inside(root, "versions/" + digest)
    if destination.exists():
        _verify(manifest, destination)
        if (destination / "manifest.json").read_bytes() != raw:
            raise ValueError("Версия manifest конфликтует с установленным набором.")
    else:
        staging = _inside(root, ".staging-" + uuid4().hex)
        staging.mkdir(parents=True, exist_ok=False)
        for resource in manifest.resources:
            target = _inside(staging, resource.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_inside(source_dir, resource.path), target)
        _verify(manifest, staging)
        (staging / "manifest.json").write_bytes(raw)
        _verify(manifest, staging)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            staging.rename(destination)
        except OSError:
            if not destination.is_dir():
                raise
            _verify(manifest, destination)
            if (destination / "manifest.json").read_bytes() != raw:
                raise ValueError("Конфликт параллельной подготовки набора.")
        # Незавершённые .staging не активируются и могут быть осмотрены/удалены отдельно.
    pointer = dict(path="versions/" + digest, manifest_sha256=digest)
    temporary = _inside(root, ".current-" + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(pointer), encoding="utf-8")
    os.replace(temporary, root / "current.json")
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description="Подготовка и проверка локальной поставки RAG без сети")
    parser.add_argument("action", choices=["prepare", "check"])
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            if not args.manifest or not args.source_dir:
                raise ValueError("Нужны --manifest и --source-dir.")
            prepare_data(
                json.loads(args.manifest.read_text(encoding="utf-8")), args.source_dir, args.data_dir
            )
        data = load_data(args.data_dir)
        print(f"Набор {data.manifest.version}: проверено ресурсов {len(data.manifest.resources)}.")
    except (ValueError, OSError, KeyError):
        parser.exit(2, "Набор не готов: проверьте manifest, права, пути и SHA ресурсов.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
