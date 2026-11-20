"""Данные активируются только после проверки прав, путей и SHA всего набора."""

from hashlib import sha256
import pytest


def manifest(path="registry.json", rights="APPROVED"):
    return dict(
        schema_version="rag-data.1",
        version="set-1",
        resources=[
            dict(
                resource_id="registry",
                kind="registry",
                version="r1",
                path=path,
                sha256=sha256(b"public-data").hexdigest(),
                source_url="https://grls.rosminzdrav.ru/GRLS.aspx",
                retrieved_at="2026-11-17T00:00:00Z",
                rights_status=rights,
                rights_basis="Синтетическая фикстура для технической проверки.",
            )
        ],
    )


def test_prepare_is_atomic_and_idempotent(tmp_path):
    from llm_rag.rag_data import prepare_data, load_data

    source = tmp_path / "source"
    source.mkdir()
    (source / "registry.json").write_bytes(b"public-data")
    root = tmp_path / "installed"
    first = prepare_data(manifest(), source, root)
    assert load_data(root).resource_path("registry").read_bytes() == b"public-data"
    assert prepare_data(manifest(), source, root) == first
    before = (root / "current.json").read_bytes()
    (source / "registry.json").write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        prepare_data(manifest() | {"version": "set-2"}, source, root)
    assert (root / "current.json").read_bytes() == before


@pytest.mark.parametrize(
    "path", ["../secret", "/absolute", "C:/secret", "folder/../../secret", "manifest.json"]
)
def test_manifest_rejects_escaping_paths(path, tmp_path):
    from llm_rag.rag_data import prepare_data

    with pytest.raises(ValueError):
        prepare_data(manifest(path), tmp_path, tmp_path / "output")


def test_unapproved_rights_do_not_copy_files(tmp_path):
    from llm_rag.rag_data import prepare_data

    with pytest.raises(ValueError):
        prepare_data(manifest(rights="PENDING"), tmp_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_corrupt_active_data_is_detected_before_use(tmp_path):
    from llm_rag.rag_data import prepare_data, load_data

    (tmp_path / "registry.json").write_bytes(b"public-data")
    root = tmp_path / "installed"
    prepare_data(manifest(), tmp_path, root)
    path = load_data(root).resource_path("registry")
    path.write_bytes(b"bad")
    with pytest.raises(ValueError):
        load_data(root)


def test_source_caches_follow_rag_data_dir_from_another_cwd(tmp_path):
    import os
    import subprocess
    import sys
    from tests.helpers import ROOT

    code = "from llm_rag.ml2 import grls,clinicalrecs; from pathlib import Path; import os; root=Path(os.environ['RAG_DATA_DIR']); assert grls.DEFAULT_CACHE==root/'cache/grls'; assert clinicalrecs.DEFAULT_CACHE==root/'cache/clinicalrecs'"
    run = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**os.environ, "RAG_DATA_DIR": str(tmp_path / "shared-data"), "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
