#!/usr/bin/env bash
set -euo pipefail
# Never enable shell tracing: credentials are supplied by the caller's environment.
# Explicit empty values override credentials from .env in the open smoke mode.
export WEB_USER="${WEB_USER:-}" WEB_PASSWORD="${WEB_PASSWORD:-}"
: "${WEB_PORT:?Export an unused WEB_PORT}"
: "${POSTGRES_DB:?Export POSTGRES_DB}"
: "${POSTGRES_USER:?Export POSTGRES_USER}"
: "${POSTGRES_PASSWORD:?Export POSTGRES_PASSWORD}"
cd "$(dirname "$0")/.."
project="${COMPOSE_PROJECT_NAME:-vrach-smoke-$(date +%s)-$$}"
dc=(docker compose -p "$project")
if [[ -n "$("${dc[@]}" ps -aq)" ]] || docker volume inspect "${project}_postgres_data" >/dev/null 2>&1; then
    echo "FAIL isolated project: name already in use"
    exit 1
fi
cleanup() {
    result=$?
    trap - EXIT
    "${dc[@]}" down --volumes --remove-orphans >/dev/null || result=1
    if [[ $result -ne 0 ]]; then echo "FAIL smoke_e2e"; fi
    exit "$result"
}
trap cleanup EXIT
"${dc[@]}" up --build -d --wait --wait-timeout 180
echo "OK stack"
"${dc[@]}" exec -T api alembic upgrade head
echo "OK migrations"
if [[ "${RUN_POSTGRES_TESTS:-0}" == "1" ]]; then
    # Legacy access tests set ENABLE_* themselves; remove higher-priority aliases.
    "${dc[@]}" exec -T -e RUN_POSTGRES_TESTS=1 -e ENABLE_APPLICATION_LIST=true api \
        env -u DOCS_ENABLED -u LIST_ENDPOINT_ENABLED -u ENABLE_API_DOCS pytest -q
    echo "OK PostgreSQL tests"
fi
"${dc[@]}" exec -T api python -m app.scripts.import_reference data/reference/demo.json --activate
echo "OK reference demo.json activated"
curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${WEB_PORT}/health" >/dev/null
echo "OK published health"
if [[ -n "$WEB_USER" && -n "$WEB_PASSWORD" ]]; then
    echo "MODE protected"
    "${dc[@]}" exec -T -e WEB_USER -e WEB_PASSWORD api python - < scripts/smoke_scenarios.py
else
    echo "MODE open (no credentials sent)"
    "${dc[@]}" exec -T -e WEB_USER= -e WEB_PASSWORD= api python - < scripts/smoke_scenarios.py
fi
"${dc[@]}" exec -T api python -m app.scripts.import_reference data/reference/demo-v2.json --activate
echo "OK reference demo-v2.json activated"
"${dc[@]}" exec -T -e WEB_USER -e WEB_PASSWORD -e SMOKE_REFERENCE_VERSION=demo-api-2 api python - < scripts/smoke_scenarios.py
echo "OK smoke_e2e"
