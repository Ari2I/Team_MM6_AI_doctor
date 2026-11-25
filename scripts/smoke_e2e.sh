#!/usr/bin/env bash
set -euo pipefail
# Never enable shell tracing: credentials are supplied by the caller's environment.
# Smoke is always offline and never inherits an operator's network mode.
export AI_MODE=template API_BUILD_TARGET=template AI_CORPUS_PATH=
export GIGACHAT_CREDENTIALS= GIGACHAT_SCOPE= GIGACHAT_CA_BUNDLE_FILE=
: "${API_PORT:?Export an unused API_PORT}"
: "${POSTGRES_DB:?Export POSTGRES_DB}"
: "${POSTGRES_USER:?Export POSTGRES_USER}"
: "${POSTGRES_PASSWORD:?Export POSTGRES_PASSWORD}"
cd "$(dirname "$0")/.."
project="${COMPOSE_PROJECT_NAME:-vrach-smoke-$(date +%s)-$$}"
dc=(docker compose --env-file /dev/null -p "$project")
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
curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${API_PORT}/health" >/dev/null
echo "OK published health"
"${dc[@]}" exec -T api python - < scripts/smoke_scenarios.py
"${dc[@]}" exec -T api python -m app.scripts.import_reference data/reference/demo-v2.json --activate
echo "OK reference demo-v2.json activated"
"${dc[@]}" exec -T -e SMOKE_REFERENCE_VERSION=demo-api-2 api python - < scripts/smoke_scenarios.py
echo "OK smoke_e2e"
