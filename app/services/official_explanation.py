"""Выбор публичных цитат и приоритетов сверки через GigaChat B2B."""

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


_REVIEW_ACTIONS = {
    "OFFICIAL_DIAGNOSIS_MISSING": (
        "Укажите диагноз из заключения перед первой покупкой блока.",
        "Сверьте границы блоков диагнозов в исходной таблице.",
    ),
    "OFFICIAL_DRUG_UNRESOLVED": (
        "Сверьте название и форму выпуска с упаковкой.",
        "Уточните МНН или регистрационное удостоверение препарата.",
    ),
    "OFFICIAL_DRUG_NOT_FOUND": (
        "Сверьте, относится ли строка к лекарственным препаратам.",
        "Уточните написание торгового названия по первичному документу.",
    ),
    "OFFICIAL_MNN_NOT_AVAILABLE": (
        "Сверьте действующие вещества по утверждённой инструкции.",
        "Уточните регистрационное удостоверение конкретной формы.",
    ),
    "OFFICIAL_SOURCE_CONFLICT": (
        "Сопоставьте область применения и редакции расходящихся документов.",
        "Передайте расхождение источников на медицинскую сверку.",
    ),
}
_DIAGNOSIS_ACTIONS = (
    "Сверьте код и название диагноза с заключением.",
    "Уточните диагноз в той строке, где начинается блок покупок.",
)
_DOCUMENT_ACTIONS = (
    "Проверьте доступность действующей редакции официального документа.",
    "Сверьте медицинское основание по действующему официальному источнику.",
)
_CLINICAL_ACTIONS = (
    "Сверьте связь покупки с назначением по медицинским документам.",
    "Попросите специалиста проверить применимость рекомендации к этой позиции.",
)


def _review_actions(reason: str) -> tuple[str, ...]:
    if reason in _REVIEW_ACTIONS:
        return _REVIEW_ACTIONS[reason]
    if reason in {"OFFICIAL_DIAGNOSIS_UNKNOWN", "OFFICIAL_DIAGNOSIS_CONFLICT", "OFFICIAL_DIAGNOSIS_UNRESOLVED"}:
        return _DIAGNOSIS_ACTIONS
    if reason.startswith("OFFICIAL_DOCUMENT_") or reason == "OFFICIAL_DIAGNOSIS_NOT_INDEXED":
        return _DOCUMENT_ACTIONS
    return _CLINICAL_ACTIONS


def verify_selection(response: dict, rows: dict[int, list[dict]]) -> tuple[dict, list[int]]:
    """Разрешить только цитаты и действия из вариантов для конкретной группы проверок."""
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


def _public_option(option: dict) -> dict:
    if "action" in option:
        return {"action": option["action"]}
    citation = option["citation"]
    return {"quote": citation["quote"][:1000], "title": citation["title"][:180],
            "location": citation["location"][:180]}


def _select(rows: dict[int, list[dict]]) -> tuple[dict, list[int], str | None, bool, int | None]:
    if not rows:
        return {}, [], None, False, None
    config = gigachat_settings()
    credentials, access_token = config["GIGACHAT_CREDENTIALS"], config["GIGACHAT_ACCESS_TOKEN"]
    if bool(credentials) == bool(access_token):
        return {}, [], "GIGACHAT_CONFIGURATION", False, None
    if not _SLOTS.acquire(blocking=False):
        return {}, [], "PROVIDER_BUSY", False, None
    deadline = monotonic() + _BUDGET_SECONDS
    attempted = Event()
    reported_tokens = []

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
                            {"option": number, **_public_option(option)}
                            for number, option in enumerate(options)
                        ],
                    } for row, options in batch.items()]
                    attempted.set()
                    try:
                        response = provider.select_official_explanations(payload)
                    finally:
                        usage = getattr(provider, "last_total_tokens", None)
                        if type(usage) is int:
                            reported_tokens.append(usage)
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
        return selected, summary, reason, attempted.is_set(), sum(reported_tokens) if reported_tokens else None
    except TimeoutError:
        return {}, [], "GIGACHAT_BUDGET_EXCEEDED", attempted.is_set(), sum(reported_tokens) if reported_tokens else None
    except Exception:
        return {}, [], "GIGACHAT_PROVIDER_ERROR", attempted.is_set(), sum(reported_tokens) if reported_tokens else None


def enrich_official(sessions, application_id, run_id: str) -> None:
    query = (select(ItemCheck).join(ApplicationItem).where(ApplicationItem.application_id == application_id)
             .order_by(ApplicationItem.row_number, ItemCheck.id))
    with sessions() as session:
        checks = list(session.scalars(query))
    targets = {}
    rows = {}
    action_groups = {}
    for check in checks:
        evidence = check.evidence
        sources = evidence.get("citations") or evidence.get("review_sources") or []
        if sources and evidence["evidence_status"] != "SOURCE_CONFLICT":
            ordinal = len(rows) + 1
            rows[ordinal] = [{"citation": source} for source in sources[:4]]
        else:
            reason = evidence["reason_code"]
            actions = _review_actions(reason)
            ordinal = action_groups.setdefault(actions, len(rows) + 1)
            if ordinal not in rows:
                rows[ordinal] = [{"action": action} for action in actions]
        targets.setdefault(ordinal, []).append(check.id)
    selected, summary_rows, reason, attempted, tokens = _select(rows)
    with sessions.begin() as session:
        application = session.get(Application, application_id, with_for_update=True)
        if application is None or application.demo_meta.get("analysis_run_id") != run_id:
            return
        saved = {check.id: check for check in session.scalars(query)}
        for ordinal, check_ids in targets.items():
            if ordinal in selected:
                option = selected[ordinal]
                update = {"effective_mode": "gigachat"}
                if "citation" in option:
                    update["model_highlight"] = option["citation"]
                else:
                    update["model_review_action"] = option["action"]
            elif reason:
                update = {"fallback_reason": reason}
            else:
                continue
            for check_id in check_ids:
                check = saved[check_id]
                check.evidence = check.evidence | update
        mode = "mixed" if selected and reason else "gigachat" if selected else "offline"
        summary = [selected[row] for row in summary_rows[:6]]
        application.demo_meta = application.demo_meta | {
            "model_attempted": attempted, "model_tokens": tokens, "effective_mode": mode, "fallback_reason": reason,
            "model_summary": [option["citation"] for option in summary if "citation" in option],
            "model_review_priorities": list(dict.fromkeys(
                option["action"] for option in summary if "action" in option
            )),
        }
