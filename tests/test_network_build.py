"""Подготовка network-образа: права, постоянный файл и отсутствие сетевых вызовов."""

import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import check_network_build as build

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def mounted_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "is_mount", lambda self: self == tmp_path)
    monkeypatch.setattr(build.os, "geteuid", lambda: 1000)
    return tmp_path


def test_probe_is_read_after_new_check_without_overwrite(mounted_data: Path) -> None:
    build.check_storage(mounted_data, check_probe=False)
    probe = mounted_data / build.PROBE
    before = probe.stat().st_mtime_ns
    build.check_storage(mounted_data, check_probe=True)
    assert probe.stat().st_mtime_ns == before
    assert (mounted_data / "cache/grls").is_dir()
    assert (mounted_data / "indexes").is_dir()
    assert list((mounted_data / "indexes").iterdir()) == []
    probe.unlink()
    with pytest.raises(FileNotFoundError):
        build.check_storage(mounted_data, check_probe=True)


def test_changed_probe_is_not_replaced(mounted_data: Path) -> None:
    probe = mounted_data / build.PROBE
    probe.write_text("different-build", encoding="utf-8")
    with pytest.raises(build.BuildCheckError, match="PROBE_CHANGED"):
        build.check_storage(mounted_data, check_probe=True)
    assert probe.read_text(encoding="utf-8") == "different-build"


def test_storage_is_checked_as_api_user(
    mounted_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build.os, "geteuid", lambda: 0)
    with pytest.raises(build.BuildCheckError, match="API_USER"):
        build.check_storage(mounted_data, check_probe=False)


def test_unmounted_path_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "is_mount", lambda self: False)
    monkeypatch.setattr(build.os, "geteuid", lambda: 1000)
    with pytest.raises(build.BuildCheckError, match="DATA_MOUNT"):
        build.check_storage(tmp_path, check_probe=False)


def test_prepare_changes_only_mount_root(
    mounted_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        build.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=1234, pw_gid=5678)
    )
    calls = []
    monkeypatch.setattr(build.os, "chown", lambda *args: calls.append(args))
    build.prepare_volume(mounted_data)
    assert calls == [(mounted_data, 1234, 5678)]
    assert list(mounted_data.iterdir()) == []


def test_prepare_requires_root(mounted_data: Path) -> None:
    with pytest.raises(build.BuildCheckError, match="VOLUME_PREPARE"):
        build.prepare_volume(mounted_data)


@pytest.mark.parametrize("scope", ["GIGACHAT_API_B2B", "GIGACHAT_API_CORP"])
def test_scope_and_revisions_are_explicit(scope: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGACHAT_SCOPE", scope)
    monkeypatch.setenv("AI_BACKEND_REVISION", "a" * 40)
    monkeypatch.setenv("AI_LIBRARY_REVISION", "b" * 40)
    monkeypatch.setenv("RAG_DATA_DIR", str(build.DATA_DIR))
    build.check_settings()
    monkeypatch.delenv("AI_LIBRARY_REVISION")
    with pytest.raises(build.BuildCheckError, match="BUILD_REVISIONS"):
        build.check_settings()


def test_personal_scope_is_not_silently_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_BACKEND_REVISION", "a" * 40)
    monkeypatch.setenv("AI_LIBRARY_REVISION", "b" * 40)
    monkeypatch.setenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    with pytest.raises(build.BuildCheckError, match="SCOPE"):
        build.check_settings()


def test_ca_check_keeps_certificate_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class Context:
        def load_verify_locations(self, *, cafile: str) -> None:
            calls.append(cafile)

        def cert_store_stats(self) -> dict[str, int]:
            return {"x509_ca": 2}

    def context(protocol: int) -> Context:
        assert protocol == ssl.PROTOCOL_TLS_CLIENT
        return Context()

    monkeypatch.setenv("GIGACHAT_CA_BUNDLE_FILE", "/external/test-ca.pem")
    monkeypatch.setattr(build.ssl, "SSLContext", context)
    assert build.check_ca() == 2
    assert calls == ["/external/test-ca.pem"]


def test_main_does_not_print_third_party_errors(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def broken() -> None:
        raise RuntimeError("PRIVATE-ERROR-MARKER")

    monkeypatch.setattr(build.sys, "addaudithook", lambda hook: None)
    monkeypatch.setattr(build, "check_settings", broken)
    assert build.main(["--write-probe"]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE-ERROR-MARKER" not in output
    assert "DEPENDENCIES_CA_OR_STORAGE" in output


def test_swallowed_network_attempt_still_fails(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    hooks = []
    monkeypatch.setattr(build.sys, "addaudithook", hooks.append)
    monkeypatch.setattr(build, "check_settings", lambda: None)

    def import_with_side_effect() -> None:
        with pytest.raises(PermissionError):
            hooks[0]("socket.getaddrinfo", ())

    monkeypatch.setattr(build, "check_dependencies", import_with_side_effect)
    monkeypatch.setattr(build, "check_ca", lambda: 2)
    monkeypatch.setattr(build, "check_storage", lambda *a, **k: None)
    assert build.main(["--write-probe"]) == 1
    assert "NETWORK_ATTEMPT" in capsys.readouterr().out
