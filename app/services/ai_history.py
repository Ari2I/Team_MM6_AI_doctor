"""История версий: только сохранение в текущей транзакции, без повторного AI-вызова."""

import json
from hashlib import sha256

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import ApplicationAIHistory, ApplicationAIResult


def archive_ai_result(session: Session, stored: ApplicationAIResult | None) -> None:
    if not isinstance(stored, ApplicationAIResult):
        return
    fingerprint = sha256(
        json.dumps(
            [stored.backend_revision, stored.ai_revision, stored.result, stored.checks],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    session.execute(
        insert(ApplicationAIHistory)
        .values(
            application_id=stored.application_id,
            fingerprint=fingerprint,
            result=stored.result,
            checks=stored.checks,
            backend_revision=stored.backend_revision,
            ai_revision=stored.ai_revision,
            saved_at=stored.saved_at,
        )
        .on_conflict_do_nothing(constraint="uq_ai_history_version")
    )
