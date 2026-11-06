"""Выбор цитируемых пояснений через GigaChat B2B по публичным фрагментам."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import os
from threading import BoundedSemaphore, Event
from time import monotonic

from dotenv import dotenv_values
from sqlalchemy import select

from app.models import Application, ApplicationItem, ItemCheck
from llm_rag.providers.base import PROVIDER_ERROR_MESSAGES, ProviderError
from llm_rag.providers.gigachat import GigaChatProvider

_BATCH_SIZE = 8
_BUDGET_SECONDS = 25
_SLOTS = BoundedSemaphore(2)
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gigachat-b2b")


def verify_selection(response: dict, rows: dict[int, list[dict]]) -> tuple[dict, list[int]]:
    """Разрешить только ссылки на фрагменты этой строки из переданного набора."""
    if not isinstance(response, dict) or set(response) != {"selections", "summary_rows"}:
        raise ValueError("Некорректные поля ответа GigaChat")
    choices, summary = response["selections"], response["summary_rows"]
    if not isinstance(choices, dict) or set(choices) != {str(row) for row in rows}:
        raise ValueError("В ответе есть пропуски или лишние строки")
    selected = {}
    for row, options in rows.items():
        option = choices[str(row)]
        if type(option) is not int or not 0 <= option < len(options):
            raise ValueError("Ответ ссылается на неизвестный фрагмент")
        selected[row] = options[option]
    if (not isinstance(summary, list) or not 1 <= len(summary) <= 3
            or any(type(row) is not int or row not in selected for row in summary)):
        raise ValueError("Некорректная сводка GigaChat")
    return selected, list(dict.fromkeys(summary))


def gigachat_settings() -> dict[str, str | None]:
    names = ("GIGACHAT_CREDENTIALS", "GIGACHAT_ACCESS_TOKEN", "GIGACHAT_MODEL", "GIGACHAT_CA_BUNDLE")
    path = os.getenv("GIGACHAT_ENV_FILE")
    try:
        configured = dotenv_values(path) if path else {}
    except (OSError, UnicodeError):
        configured = {}
    return {name: os.getenv(name, configured.get(name)) for name in names}


def _select(rows: dict[int, list[dict]]) -> tuple[dict, list[int], str | None, bool]:
    if not rows:
        return {}, [], None, False
    config = gigachat_settings()
    credentials, access_token = config["GIGACHAT_CREDENTIALS"], config["GIGACHAT_ACCESS_TOKEN"]
    if bool(credentials) == bool(access_token):
        return {}, [], "GIGACHAT_CONFIGURATION", False
    if not _SLOTS.acquire(blocking=False):
        return {}, [], "PROVIDER_BUSY", False
    deadline = monotonic() + _BUDGET_SECONDS
    attempted = Event()

    def run():
        selected, summary = {}, []
        keys = list(rows)
        try:
            with GigaChatProvider(
                credentials, access_token=access_token, enabled=True,
                scope="GIGACHAT_API_B2B", model=config["GIGACHAT_MODEL"] or "GigaChat-2",
                ca_bundle=config["GIGACHAT_CA_BUNDLE"] or None, timeout=8,
            ) as provider:
                for start in range(0, len(keys), _BATCH_SIZE):
                    if monotonic() >= deadline - 8:
                        return selected, summary, "GIGACHAT_BUDGET_EXCEEDED"
                    batch = {row: rows[row] for row in keys[start:start + _BATCH_SIZE]}
                    # Порядковые номера локальны для вызова. Цены, диагнозы и текст заявки не передаются.
                    payload = [{
                        "row": row, "options": [
                            {"option": number, "quote": citation["quote"][:1000],
                             "title": citation["title"][:180], "location": citation["location"][:180]}
                            for number, citation in enumerate(citations)
                        ],
                    } for row, citations in batch.items()]
                    attempted.set()
                    response = provider.select_official_explanations(payload)
                    try:
                        highlights, summary_rows = verify_selection(response, batch)
                    except ValueError:
                        return selected, summary, "GIGACHAT_UNVERIFIED_RESPONSE"
                    selected.update(highlights)
                    summary.extend(summary_rows)
        except (OSError, ValueError):
            return selected, summary, "GIGACHAT_CONFIGURATION"
        except ProviderError as error:
            code = str(error)
            return selected, summary, code if code in PROVIDER_ERROR_MESSAGES else "GIGACHAT_PROVIDER_ERROR"
        return selected, summary, None

    future = _POOL.submit(run)
    future.add_done_callback(lambda _: _SLOTS.release())
    try:
        selected, summary, reason = future.result(timeout=_BUDGET_SECONDS)
        return selected, summary, reason, attempted.is_set()
    except TimeoutError:
        return {}, [], "GIGACHAT_BUDGET_EXCEEDED", attempted.is_set()
    except Exception:
        return {}, [], "GIGACHAT_PROVIDER_ERROR", attempted.is_set()


def enrich_official(sessions, application_id, run_id: str) -> None:
    query = (select(ItemCheck).join(ApplicationItem).where(ApplicationItem.application_id == application_id)
             .order_by(ApplicationItem.row_number, ItemCheck.id))
    with sessions() as session:
        checks = list(session.scalars(query))
    targets = {}
    rows = {}
    for check in checks:
        evidence = check.evidence
        sources = evidence.get("citations") or evidence.get("review_sources") or []
        if sources and evidence["evidence_status"] != "SOURCE_CONFLICT":
            ordinal = len(rows) + 1
            targets[ordinal] = check.id
            rows[ordinal] = sources[:4]
    selected, summary_rows, reason, attempted = _select(rows)
    with sessions.begin() as session:
        application = session.get(Application, application_id, with_for_update=True)
        if application is None or application.demo_meta.get("analysis_run_id") != run_id:
            return
        for ordinal, check_id in targets.items():
            check = session.get(ItemCheck, check_id)
            if ordinal in selected:
                check.evidence = check.evidence | {
                    "model_highlight": selected[ordinal], "effective_mode": "gigachat",
                }
            elif reason:
                check.evidence = check.evidence | {"fallback_reason": reason}
        mode = "mixed" if selected and reason else "gigachat" if selected else "offline"
        application.demo_meta = application.demo_meta | {
            "model_attempted": attempted, "effective_mode": mode, "fallback_reason": reason,
            "model_summary": [selected[row] for row in summary_rows[:6]],
        }
