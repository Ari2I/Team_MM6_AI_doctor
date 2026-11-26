from contextlib import contextmanager

import httpx
import pytest

from llm_rag import integration_budget
from llm_rag.ml2 import grls, official


def test_registry_rechecks_deadline_before_archive(monkeypatch, tmp_path):
    now = [10.0]
    calls = []
    monkeypatch.setattr(integration_budget, "monotonic", lambda: now[0])

    class Site:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def stream(self, method, url, *, timeout):
            calls.append(timeout.read)
            if url == grls.GRLS_PAGE:
                now[0] += 2
                page = b"""<button onclick="go('GetGRLS.ashx?FileGUID=01234567-0123-0123-0123-0123456789ab&amp;UserReq=1')"></button>"""
                yield httpx.Response(200, content=page, request=httpx.Request(method, url))
            else:
                now[0] += 3
                yield httpx.Response(200, content=b"archive", request=httpx.Request(method, url))

    monkeypatch.setattr(grls.httpx, "Client", Site)
    with pytest.raises(TimeoutError):
        grls.download_latest(tmp_path, deadline=15)
    assert calls == [5, 3]
    assert not list(tmp_path.iterdir())


def test_clinical_download_uses_remaining_time(monkeypatch):
    now = [10.0]
    calls = []
    monkeypatch.setattr(integration_budget, "monotonic", lambda: now[0])

    @contextmanager
    def stream(method, url, *, params, timeout):
        calls.append(timeout)
        yield httpx.Response(200, json=[{}], request=httpx.Request(method, url))

    monkeypatch.setattr(official.httpx, "stream", stream)
    source = official.ClinicalSources(deadline=15)
    assert source.catalog == [{}]
    now[0] += 4
    assert official._get("GetClinrec", deadline=source.deadline, id="1_1") == [{}]
    now[0] += 1
    with pytest.raises(TimeoutError):
        official._get("GetClinrec", deadline=source.deadline, id="2_1")
    assert calls == [5, 1]
