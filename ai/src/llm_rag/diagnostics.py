"""Технический журнал без сообщений исключений, данных заявки и секретов."""

from datetime import datetime, timezone
from pathlib import Path
import traceback
import httpx

from llm_rag.providers.base import PROVIDER_ERROR_MESSAGES, ProviderError


class StaffReportError(ValueError):
    """Фиксированный код нарушения локального контракта."""


def safe_diagnostic(error, *, stage, elapsed_ms=0):
    """Ошибка журнала не должна заменять исходное исключение."""
    try:
        return _collect(error, stage=stage, elapsed_ms=elapsed_ms)
    except Exception:
        return dict(stage=stage, code='DIAGNOSTIC_COLLECTION_FAILED', exception_chain=[], traceback=[])


def _collect(error, *, stage, elapsed_ms):
    """Сохранить типы и позиции стека; не читать локальные переменные и raw body."""
    staff_codes = {'STAFF_PACKET_TOO_LARGE', 'STAFF_PAIR_COVERAGE', 'STAFF_IMMUTABLE_FACT_CHANGED',
                   'STAFF_UNKNOWN_REFERENCE', 'STAFF_UNSUPPORTED_NUMBER', 'STAFF_FORBIDDEN_ADVICE', 'STAFF_CONTRADICTED_FACT'}
    code = 'UNEXPECTED_ERROR'
    if isinstance(error, (StaffReportError, ProviderError)) and len(error.args) == 1:
        candidate = error.args[0]
        if type(candidate) is str and candidate in staff_codes | PROVIDER_ERROR_MESSAGES.keys():
            code = candidate
    result = dict(timestamp=datetime.now(timezone.utc).isoformat(), stage=stage,
                  code=code, elapsed_ms=round(elapsed_ms, 1), exception_chain=[], traceback=[])
    current, seen = error, set()
    while current is not None and id(current) not in seen and len(seen) < 10:
        seen.add(id(current))
        result['exception_chain'].append(type(current).__name__)
        for frame in traceback.extract_tb(current.__traceback__):
            result['traceback'].append(dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name))
        try:
            request = getattr(current, 'request', None)
        except RuntimeError:
            request = None
        if isinstance(request, httpx.Request):
            path = request.url.path
            if path in ('/api/v2/oauth', '/chat/completions', '/v1/chat/completions'):
                result['endpoint'] = 'oauth' if path == '/api/v2/oauth' else 'completions'
        current = current.__cause__ or current.__context__
    return result
