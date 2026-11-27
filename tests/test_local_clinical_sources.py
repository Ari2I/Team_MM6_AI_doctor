"""Синтетический прежний кэш: настоящий ML-2, без SDK, сети и покупочных заглушек."""

import base64
import gzip
import json
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from app.config import AISettings
from app.schemas.analysis import PairCheck
from app.services.ai_adapter import AIAdapter, AIInput
from app.services.local_clinical_sources import LocalClinicalSources

AI_SOURCE = Path("ai/src").resolve()
pytestmark = [
    pytest.mark.skipif(not AI_SOURCE.is_dir(), reason="AI library is absent"),
    pytest.mark.usefixtures("no_network"),
]


@pytest.fixture(autouse=True)
def library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(AI_SOURCE))
    monkeypatch.setitem(sys.modules, "gigachat", None)


@pytest.fixture
def local_data(tmp_path: Path) -> tuple[Path, Path]:
    from llm_rag.ml2.clinicalrecs import BASE_URL
    from llm_rag.ml2.registry import RegistryEntry, RegistrySnapshot, RegistrySource

    now = datetime.now(UTC)
    source = RegistrySource(
        title="Синтетический реестр",
        url="https://grls.rosminzdrav.ru/GRLS.aspx",
        version="synthetic-1",
        retrieved_at=now.date(),
        location="строка 1",
        quote="Синтетическое вещество",
    )
    registry = RegistrySnapshot(
        version="synthetic-1",
        as_of=now.date(),
        registrations=(
            RegistryEntry(
                registration="TEST",
                trade_name="Синтетический препарат",
                mnn="Синтетическое вещество",
                source=source,
            ),
        ),
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(registry.model_dump_json(), encoding="utf-8")
    cache = tmp_path / "clinical"
    cache.mkdir()
    data = {"RU": [["Синтетическая КР", "1_1", "2024", 3, 3, [], "J45", [], [], "", ""]]}
    packed = base64.b64encode(gzip.compress(json.dumps(data).encode())).decode()
    chunks = [packed[i * len(packed) // 11 : (i + 1) * len(packed) // 11] for i in range(11)]
    catalog = '<script>const KRP = {"1":"test-1"}; const krPage = x => x;</script>'
    catalog += "".join(f'<script id="krdata{i}">{part}</script>' for i, part in enumerate(chunks))
    (cache / "catalog-ru.json").write_text(
        json.dumps({"checked_at": now.isoformat(), "html": catalog}), encoding="utf-8"
    )
    html = """<h1>Синтетическая КР</h1><dl class="krt-pass">
    <dt>ID:</dt><dd>1_1</dd><dt>Кодирование по МКБ-10:</dt><dd>J45</dd>
    <dt>Возрастная категория:</dt><dd>Взрослые, дети</dd>
    <dt>Год утверждения:</dt><dd>2024</dd><dt>Статус:</dt><dd>Действует</dd>
    <dt>Статус применения:</dt><dd>Применяется</dd></dl>
    <p class="krt-srcl">Источник: Рубрикатор Минздрава России</p>
    <a href="https://cr.minzdrav.gov.ru/preview-cr/1_1">Оригинал</a>
    <h2 id="s3">3. Лечение</h2><ul id="r1" class="krt-r">
    <li>Синтетическое вещество: тестовое описание, не медицинская рекомендация.</li></ul>
    <h2 id="s4">4. Реабилитация</h2>"""
    key = sha256((BASE_URL + "/kr/test-1").encode()).hexdigest()[:24]
    (cache / f"{key}.html").write_text(html, encoding="utf-8")
    (cache / f"{key}.json").write_text(
        json.dumps(
            {
                "checked_at": now.isoformat(),
                "retrieved_at": now.isoformat(),
                "sha256": sha256(html.encode()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return registry_path, cache


def test_offline_uses_verified_fragments_without_sdk(local_data: tuple[Path, Path]) -> None:
    registry, cache = local_data
    before = {p.name: p.read_bytes() for p in cache.iterdir()}
    adapter = AIAdapter(
        AISettings(
            mode="offline",
            registry_path=str(registry),
            clinical_cache_dir=str(cache),
            live_sources=True,
        )
    )
    pair = AIInput(drug_name="Синтетический препарат", diagnosis="J45.9", age_group="adult")
    check = adapter.analyze(pair, PairCheck())
    assert adapter.last_mnn == "Синтетическое вещество"
    assert adapter.last_diagnostics["fragment_count"] == 1
    assert adapter.last_diagnostics["candidate_count"] == 1
    assert adapter.last_diagnostics["ml2_status"] == "NEEDS_REVIEW"
    assert adapter.last_diagnostics["model_calls"] == 0
    assert adapter.last_diagnostics["cache_states"] == ["FRESH_CACHE"]
    assert check.category is None and check.needs_review
    assert check.review_reason == "AI_REVIEW_REQUIRED"
    assert "1_1" in check.reference_version
    assert pair.dosage is None and pair.quantity is None and pair.amount is None
    assert before == {p.name: p.read_bytes() for p in cache.iterdir()}


def test_offline_preserves_all_fragments_in_synthetic_cache(
    local_data: tuple[Path, Path],
) -> None:
    registry, cache = local_data
    path = next(cache.glob("*.html"))
    html = path.read_text(encoding="utf-8")
    extra = "".join(
        f'<ul class="krt-r" id="extra-{number}">'
        f"<li>Синтетическое вещество: описание примера {number}.</li></ul>"
        for number in range(5)
    )
    html = html.replace("</ul>", "</ul>" + extra, 1)
    path.write_text(html, encoding="utf-8")
    metadata_path = path.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["sha256"] = sha256(html.encode()).hexdigest()
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in cache.iterdir()}
    adapter = AIAdapter(
        AISettings(
            mode="offline",
            registry_path=str(registry),
            clinical_cache_dir=str(cache),
            live_sources=True,
        )
    )
    pair = AIInput(drug_name="Синтетический препарат", diagnosis="J45.9", age_group="adult")
    check = adapter.analyze(pair, PairCheck())
    assert adapter.last_diagnostics["fragment_count"] == 6
    assert adapter.last_diagnostics["ml2_status"] == "NEEDS_REVIEW"
    assert adapter.last_diagnostics["model_calls"] == 0
    assert check.category is None and check.needs_review
    assert before == {p.name: p.read_bytes() for p in cache.iterdir()}


@pytest.mark.parametrize("damage", ["missing", "expired", "future", "hash", "invalid_json"])
def test_cache_errors_never_refresh_or_leak(
    local_data: tuple[Path, Path],
    damage: str,
) -> None:
    registry, cache = local_data
    catalog = cache / "catalog-ru.json"
    marker = "PRIVATE-CACHE-ERROR-MARKER"
    if damage == "missing":
        catalog.unlink()
    elif damage in {"expired", "future"}:
        data = json.loads(catalog.read_text())
        data["checked_at"] = (
            datetime.now(UTC) + timedelta(days=2 if damage == "future" else -2)
        ).isoformat()
        catalog.write_text(json.dumps(data), encoding="utf-8")
    elif damage == "invalid_json":
        catalog.write_text(marker, encoding="utf-8")
    else:
        path = next(cache.glob("*.html"))
        path.write_text(marker, encoding="utf-8")
    adapter = AIAdapter(
        AISettings(
            mode="offline",
            registry_path=str(registry),
            clinical_cache_dir=str(cache),
            live_sources=True,
        )
    )
    check = adapter.analyze(
        AIInput(drug_name="Синтетический препарат", diagnosis="J45.9", age_group="adult"),
        PairCheck(),
    )
    assert check.category is None and check.needs_review
    assert check.review_reason == "AI_UNAVAILABLE"
    assert adapter.last_diagnostics["data_error"] == "CLINICAL_CACHE_INVALID"
    assert marker not in check.model_dump_json()


def test_catalog_constructor_has_empty_policy_and_no_io(tmp_path: Path) -> None:
    sources = LocalClinicalSources(tmp_path / "absent")
    assert not sources.network_policy.diagnoses and not sources.network_policy.inns
    assert not sources.cache.exists() and not sources.loaded


def test_uncertain_edition_is_review_without_model(
    local_data: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Два равноправных паспорта нельзя выбирать самостоятельно.
    original = LocalClinicalSources.candidates

    def duplicate(self: LocalClinicalSources, code: str, age: str | None) -> tuple:
        candidates = original(self, code, age)
        return candidates + candidates

    monkeypatch.setattr(LocalClinicalSources, "candidates", duplicate)
    registry, cache = local_data
    from app.services.ml2_analysis import analyze_pair

    result = analyze_pair(
        AIInput(drug_name="Синтетический препарат", diagnosis="J45.9", age_group="adult"),
        PairCheck(),
        AISettings(
            mode="offline",
            registry_path=str(registry),
            clinical_cache_dir=str(cache),
            live_sources=True,
        ),
        20,
    )
    assert result.check["needs_review"] and result.check["category"] is None
    assert result.check["review_reason"] == "AI_REVIEW_REQUIRED"
    assert result.diagnostics["ml2_status"] == "NEEDS_CONTEXT"
    assert result.diagnostics["model_calls"] == 0
