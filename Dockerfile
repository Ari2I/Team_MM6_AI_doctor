FROM python:3.11-slim AS backend-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home appuser && chown appuser:appuser /app

COPY --chown=appuser:appuser pyproject.toml README.md ./
COPY --chown=appuser:appuser app ./app
RUN pip install --no-cache-dir ".[dev]"

COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser tests ./tests
COPY --chown=appuser:appuser data ./data
COPY --chown=appuser:appuser ai/examples/corpus.json ./ai/examples/corpus.json
COPY --chown=appuser:appuser scripts ./scripts

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Лексический offline-путь не импортирует torch/transformers и не скачивает веса.
FROM backend-base AS ai-offline
COPY --chown=appuser:appuser ai/src ./ai/src

FROM ai-offline AS ai-network
USER root
RUN pip install --no-cache-dir ".[ai-network]"
USER appuser

# Последняя стадия — также default для обычного docker build.
FROM backend-base AS template
