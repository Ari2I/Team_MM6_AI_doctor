FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN useradd --create-home appuser && chown appuser:appuser /app

COPY --chown=appuser:appuser pyproject.toml README.md ./
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser src ./src
RUN pip install --no-cache-dir ".[dev]"

COPY --chown=appuser:appuser alembic.ini run.py check_local.py requirements.txt ./
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser tests ./tests
COPY --chown=appuser:appuser data ./data
COPY --chown=appuser:appuser examples ./examples
COPY --from=frontend --chown=appuser:appuser /build/dist ./frontend/dist

USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
