"""Приёмка должна отклонять fallback и не зависеть от медицинского ответа модели."""

import json
import logging
import sys
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from openpyxl import Workbook

from scripts import prepare_network_case as prepare
from scripts import run_network_acceptance as run
from scripts import verify_network_acceptance as verify
from scripts.network_case_checks import full_inn_pattern, normalize_public_text
from tests.ai_report_data import human_report

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(autouse=True)
def restore_cli_logging():
    previous = logging.root.manager.disable
    yield
    logging.disable(previous)


def control_result():
    document = dict(
        inn="Синтетическое вещество",
        diagnosis="Синтетический диагноз",
        text="Синтетическое вещество упоминается в публичном фрагменте.",
    )
    prefix = f"МНН: {document['inn']}. Диагноз: {document['diagnosis']}."
    key = sha256(prefix.encode()).hexdigest()
    value = dict(
        completeness="COMPLETE",
        unprocessed_pairs=[],
        pairs=[
            dict(
                inn=document["inn"],
                generation_mode="MODEL",
                staff_analysis={},
                citations=[dict(quote=document["text"])],
                reasons=["NEEDS_REVIEW"],
            )
        ],
        evidence_snapshot=dict(documents=[document]),
        retrieval_info=dict(method="vector", embedding_revision="verified-revision"),
        manifest=dict(registry={"version": "synthetic-1"}, clinical={"version": "synthetic-1"}),
        usage=dict(calls=4),
    )
    return value, {key}


@pytest.mark.parametrize(
    ("inn", "text", "matches"),
    [
        pytest.param("Вещество альфа", "Вещество альфа** упомянуто.", True, id="full-name"),
        pytest.param("Вещество альфа", "(ВЕЩЕСТВО\u00a0альфа).", True, id="case-and-space"),
        pytest.param("Вещество альфа", "Вещество\n  альфа.", True, id="line-wrap"),
        pytest.param("Вёщество альфа", "Вещество альфа.", True, id="yo"),
        pytest.param("Вещество альфа", "Другое альфа.", False, id="shared-token"),
        pytest.param("Вещество альфа", "Вещество упомянуто.", False, id="missing-second-word"),
        pytest.param("Вещество альфа", "Альфа вещество.", False, id="word-order"),
        pytest.param("Вещество альфа", "Вещество, альфа.", False, id="separate-terms"),
        pytest.param("Вещество", "Сверхвещество.", False, id="prefix"),
        pytest.param("Вещество", "Веществом.", False, id="inflection"),
        pytest.param("Вещество", "Вещество-альфа.", False, id="compound-suffix"),
        pytest.param("Вещество", "Другое-вещество.", False, id="compound-prefix"),
        pytest.param("Вещество альфа", "Вeщество альфа.", False, id="latin-lookalike"),
        pytest.param("", "Любой текст.", False, id="empty"),
        pytest.param(" \n", "Любой текст.", False, id="whitespace"),
    ],
)
def test_control_requires_full_inn(inn, text, matches):
    pattern = full_inn_pattern(inn)
    assert bool(pattern and pattern.search(normalize_public_text(text))) is matches


def test_selection_skips_shared_token_before_loading_fragments():
    wrong = SimpleNamespace(mnn="Авещество альфа", form="Форма", trade_name="Первый")
    correct = SimpleNamespace(mnn="Бвещество альфа", form="Форма", trade_name="Второй")
    text = "Бвещество альфа приведено в публичном тексте."
    recommendation = SimpleNamespace(segments=[SimpleNamespace(text=text)])
    loaded = []

    def fragments(rec, inn, **kwargs):
        loaded.append(inn)
        assert rec is recommendation
        return [SimpleNamespace(text=text)]

    entries = {entry.trade_name: entry for entry in (wrong, correct)}
    index = SimpleNamespace(resolve=lambda name, form: entries[name])
    selected = prepare.select_control_entry(
        [wrong, correct], recommendation=recommendation, index=index, load_fragments=fragments
    )
    assert selected is correct and loaded == [correct.mnn]


@pytest.mark.parametrize(
    "damage",
    ["no-fragments", "wrong-fragment", "long-fragment", "ambiguous-registry", "split-name"],
)
def test_selection_rejects_unlinked_control(damage):
    entry = SimpleNamespace(mnn="Вещество альфа", form="Форма", trade_name="Препарат")
    text = "Вещество альфа приведено в публичном тексте."
    segments = [SimpleNamespace(text=text)]
    fragments = [SimpleNamespace(text=text)]
    resolved = entry
    if damage == "no-fragments":
        fragments = []
    elif damage == "wrong-fragment":
        fragments.append(SimpleNamespace(text="Другое альфа."))
    elif damage == "long-fragment":
        fragments = [SimpleNamespace(text=text + "я" * 1600)]
    elif damage == "ambiguous-registry":
        resolved = SimpleNamespace(mnn=None)
    else:
        segments = [SimpleNamespace(text="Вещество"), SimpleNamespace(text="альфа")]
    with pytest.raises(ValueError, match="NO_CONTROL_PAIR"):
        prepare.select_control_entry(
            [entry],
            recommendation=SimpleNamespace(segments=segments),
            index=SimpleNamespace(resolve=lambda name, form: resolved),
            load_fragments=lambda *args, **kwargs: fragments,
        )


def test_assessment_accepts_only_fresh_control_vectors():
    result, fresh = control_result()
    verify.assess(result, new_vectors=fresh)
    with pytest.raises(ValueError, match="NO_FRESH_CONTROL_VECTORS"):
        verify.assess(result, new_vectors={"unrelated"})


@pytest.mark.parametrize("damage", ["wrong-inn", "wrong-document", "wrong-quote", "missing-inn"])
def test_assessment_rejects_evidence_for_another_inn(damage):
    result, fresh = control_result()
    if damage == "wrong-inn":
        result["evidence_snapshot"]["documents"][0]["inn"] = "Другое вещество"
    elif damage == "wrong-document":
        result["evidence_snapshot"]["documents"][0]["text"] = "Другое вещество."
    elif damage == "wrong-quote":
        result["pairs"][0]["citations"].append(dict(quote="Другое вещество."))
    else:
        result["pairs"][0]["inn"] = None
    with pytest.raises(ValueError, match="UNRELATED_CONTROL_(EVIDENCE|CITATION)"):
        verify.assess(result, new_vectors=fresh)


@pytest.mark.parametrize(
    "damage",
    [
        "partial",
        "pending",
        "fallback",
        "offline",
        "no_staff",
        "source",
        "no_citations",
        "no_snapshot",
        "lexical",
        "unverified",
        "no_fresh",
        "no_manifest",
    ],
)
def test_fallback_cannot_pass_assessment(damage):
    result, fresh = control_result()
    if damage == "partial":
        result["completeness"] = "PARTIAL"
    elif damage == "pending":
        result["unprocessed_pairs"] = [{}]
    elif damage == "fallback":
        result["pairs"][0]["generation_mode"] = "FALLBACK"
    elif damage == "offline":
        result["pairs"][0]["generation_mode"] = "OFFLINE"
    elif damage == "no_staff":
        result["pairs"][0]["staff_analysis"] = None
    elif damage == "source":
        result["pairs"][0]["reasons"] = ["SOURCE_ERROR"]
    elif damage == "no_citations":
        result["pairs"][0]["citations"] = []
    elif damage == "no_snapshot":
        result["evidence_snapshot"] = None
    elif damage == "lexical":
        result["retrieval_info"]["method"] = "lexical"
    elif damage == "unverified":
        result["retrieval_info"]["embedding_revision"] = "unverified:model"
    elif damage == "no_fresh":
        fresh = set()
    else:
        result["manifest"]["registry"] = None
    with pytest.raises(ValueError):
        verify.assess(result, new_vectors=fresh)


def test_control_copies_public_form_and_marks_test_numbers(tmp_path):
    rec = SimpleNamespace(
        title="Синтетический диагноз", edition_id="7_2", primary_url="https://example.org/7_2"
    )
    entry = SimpleNamespace(
        trade_name="Синтетический препарат",
        form="Исходное обозначение формы",
        mnn="Синтетическое вещество",
    )
    folder = tmp_path / "acceptance"
    prepare.write_case(
        folder,
        code="J45.9",
        group="adult",
        recommendation=rec,
        entry=entry,
        registry_version="synthetic-1",
    )
    from app.services.excel_parser import parse_excel
    from app.services.validator import validate_parse_result

    parsed = parse_excel((folder / "control.xlsx").read_bytes())
    assert validate_parse_result(parsed, []).is_valid
    assert len(parsed.items) == 1
    assert parsed.items[0].dosage == entry.form
    assert parsed.items[0].quantity == 1 and parsed.items[0].amount == 1000
    policy = json.loads((folder / "network-terms.json").read_text())
    assert policy["diagnosis_aliases"] == [
        dict(name="J45.9 Синтетический диагноз", icd10="J45.9", edition="7_2")
    ]
    case = json.loads((folder / "case.json").read_text())
    assert (
        case["synthetic"]
        and case["workbook_sha256"] == sha256((folder / "control.xlsx").read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="CASE_EXISTS"):
        prepare.write_case(
            folder,
            code="J45.9",
            group="adult",
            recommendation=rec,
            entry=entry,
            registry_version="synthetic-1",
        )


def test_case_is_not_published_on_write_error(tmp_path, monkeypatch):
    def broken(folder, **kwargs):
        (folder / "partial").touch()
        raise ValueError

    monkeypatch.setattr(prepare, "_write_case", broken)
    with pytest.raises(ValueError):
        prepare.write_case(tmp_path / "acceptance")
    assert list(tmp_path.iterdir()) == []


def report_value():
    return dict(
        schema_version="ai-report.1",
        application_id=str(UUID(int=1)),
        saved_at="2026-11-27T00:00:00Z",
        backend_revision="a" * 40,
        ai_revision="b" * 40,
        completeness="COMPLETE",
        human_report=human_report(),
        checks=[
            dict(
                number=1,
                item_id=str(UUID(int=2)),
                diagnosis_id=str(UUID(int=3)),
                row_number=2,
                backend_rule={},
                conflict=False,
                processed=True,
                reasons=["NEEDS_REVIEW"],
            )
        ],
    )


def prepared_files(folder):
    folder.mkdir()
    content = b"synthetic-workbook"
    (folder / "control.xlsx").write_bytes(content)
    (folder / "case.json").write_text(
        json.dumps(
            dict(
                schema_version="network-case.1",
                synthetic=True,
                age_group="adult",
                workbook_sha256=sha256(content).hexdigest(),
            )
        )
    )


def test_upload_then_read_only_get_without_reanalysis(tmp_path):
    folder = tmp_path / "control"
    prepared_files(folder)
    report = report_value()
    card = {"application": {"id": str(UUID(int=1)), "status": "analyzed"}}
    calls = []
    workbook = Workbook()
    workbook.active.title = "Сводка"
    workbook.create_sheet("Препараты")
    workbook.create_sheet("Предупреждения")
    excel = BytesIO()
    workbook.save(excel)
    workbook.close()

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert b"age_group" in request.content
            return httpx.Response(201, json=card)
        if request.url.path.endswith("report.xlsx"):
            return httpx.Response(200, content=excel.getvalue())
        if request.url.path.endswith("ai-report"):
            return httpx.Response(200, json=report)
        return httpx.Response(200, json=card)

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(handler)
    ) as client:
        run.exercise(client, folder, reopen=False)
        calls.clear()
        run.exercise(client, folder, reopen=True)
        assert calls and all(method == "GET" for method, _ in calls)
        changed = deepcopy(report)
        changed["human_report"]["summary"] = "Синтетическое изменение"
        report.update(changed)
        with pytest.raises(ValueError, match="REPORT_CHANGED"):
            run.exercise(client, folder, reopen=True)


def test_no_report_still_keeps_identifier_for_safe_diagnostics(tmp_path):
    folder = tmp_path / "control"
    prepared_files(folder)
    card = {"application": {"id": str(UUID(int=1)), "status": "analyzed"}}
    with httpx.Client(
        base_url="http://localhost",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(201, json=card) if r.method == "POST" else httpx.Response(409)
        ),
    ) as client:
        with pytest.raises(ValueError, match="AI_REPORT_NOT_SAVED"):
            run.exercise(client, folder, reopen=False)
    assert json.loads((folder / "state.json").read_text())["application_id"] == str(UUID(int=1))


def test_persistence_checks_full_result_without_ai(tmp_path, monkeypatch):
    from app.models import ApplicationAIResult

    result = {"synthetic": True}
    receipt = dict(
        files={"synthetic": "hash"},
        application_id=str(UUID(int=1)),
        result_sha256=run.digest(result),
    )
    (tmp_path / "resources-after.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(verify, "FOLDER", tmp_path)
    monkeypatch.setattr(verify, "files_manifest", lambda: receipt["files"])
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    monkeypatch.setitem(sys.modules, "gigachat", None)

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, model, identifier):
            assert model is ApplicationAIResult and identifier == UUID(int=1)
            return SimpleNamespace(result=result)

    monkeypatch.setitem(sys.modules, "app.db.session", SimpleNamespace(SessionLocal=Session))
    verify.check_persistence()
    result["changed"] = True
    with pytest.raises(ValueError, match="SAVED_RESULT_CHANGED"):
        verify.check_persistence()


@pytest.mark.parametrize("blocked", [False, True])
def test_egress_check_does_not_report_success_for_open_connection(tmp_path, monkeypatch, blocked):
    (tmp_path / "resources-after.json").write_text(json.dumps(dict(control_ip="192.0.2.1")))
    monkeypatch.setattr(verify, "FOLDER", tmp_path)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def connect(*args, **kwargs):
        if blocked:
            raise OSError("PRIVATE-ERROR-MARKER")
        return Connection()

    monkeypatch.setattr(verify.socket, "create_connection", connect)
    assert verify.main(["--check-egress-blocked"]) == (0 if blocked else 1)


@pytest.mark.parametrize("action", ["prepare", "baseline"])
def test_cli_hides_external_exception_text(monkeypatch, capsys, action):
    def broken(*args):
        raise RuntimeError("PRIVATE-ERROR-MARKER")

    if action == "prepare":
        monkeypatch.setattr(prepare, "prepare", broken)
        status = prepare.main([])
    else:
        monkeypatch.setattr(verify, "baseline", broken)
        status = verify.main(["--baseline"])
    assert status == 1 and "PRIVATE-ERROR-MARKER" not in capsys.readouterr().out
