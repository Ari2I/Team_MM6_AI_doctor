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
echo "OK smoke_e2e"
