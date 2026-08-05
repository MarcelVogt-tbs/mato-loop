#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="docker-compose.dev.yaml"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

compose() {
    local args=(docker compose --env-file .env.default --env-file .env.dev)
    if [[ -f .env.dev.override ]]; then
        args+=(--env-file .env.dev.override)
    fi
    args+=(-f "$COMPOSE_FILE" "$@")
    "${args[@]}"
}

usage() {
    printf '%s\n' \
        "Usage: ./scripts/dev.sh <command> [args]" \
        "" \
        "Commands:" \
        "  up [--build] [-d]   Start the development stack" \
        "  stop [service]      Stop services" \
        "  down                Remove containers" \
        "  reset-storage       Remove containers and volumes" \
        "  logs [service]      Show logs" \
        "  status              Show service status" \
        "  doctor              Check pgvector and the embedding index" \
        "  db [args]           Run Alembic" \
        "  lock backend        Update the backend lockfile" \
        "  check [target]      Check the backend and/or frontend" \
        "  test [target]       Test the backend, frontend, or local RAG stack" \
        "  eval-prompts [args] Run serial live prompt evaluations"
}

run_backend() {
    compose run --rm --no-deps backend sh -lc "$1"
}

run_frontend() {
    compose run --rm --no-deps frontend sh -lc "$1"
}

check_backend() {
    run_backend "uv sync --frozen --group docs && uv run --no-sync mkdocs build --strict --config-file /workspace/mkdocs.yml --site-dir /tmp/mato-loop-docs && uv run python -m compileall src/mato_loop_backend && uv run ruff check src tests"
}

check_frontend() {
    run_frontend "yarn install --immutable && yarn lint && yarn build"
}

test_backend() {
    run_backend "uv sync --frozen && uv run pytest"
}

test_frontend() {
    run_frontend "yarn install --immutable && yarn test"
}

test_rag() {
    compose up -d --wait --wait-timeout 600 db embedding
    run_backend "uv sync --frozen && MATO_LOOP_RAG_INTEGRATION=1 uv run pytest tests/test_memory_rag_integration.py"
}

run_target() {
    local operation="$1"
    local target="${2:-all}"
    case "$operation:$target" in
        check:all) check_backend; check_frontend ;;
        check:backend) check_backend ;;
        check:frontend) check_frontend ;;
        test:all) test_backend; test_frontend ;;
        test:backend) test_backend ;;
        test:frontend) test_frontend ;;
        test:rag) test_rag ;;
        *) printf 'Unbekanntes Target: %s\n' "$target" >&2; exit 1 ;;
    esac
}

cd "$PROJECT_ROOT"

case "${1:-}" in
    up) shift; compose up "$@" ;;
    stop) shift; compose stop "$@" ;;
    down) shift; compose down "$@" ;;
    reset-storage) compose down -v ;;
    logs) shift; compose logs -f "$@" ;;
    status) compose ps ;;
    doctor)
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extversion AS vector_version FROM pg_extension WHERE extname = '\''vector'\'';"'
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT format_type(a.atttypid, a.atttypmod) AS embedding_type FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = '\''memory_embeddings'\'' AND a.attname = '\''embedding'\'';"'
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) AS memory_count FROM memories;"'
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT status, count(*) FROM memory_embeddings GROUP BY status ORDER BY status;"'
        compose exec -T embedding python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:7997/health', timeout=5).read().decode())"
        ;;
    db) shift; compose run --rm backend uv run alembic "$@" ;;
    lock)
        if [[ "${2:-}" != "backend" ]]; then
            printf 'Unbekanntes Lock-Target: %s\n' "${2:-}" >&2
            exit 1
        fi
        compose run --rm --no-deps backend uv lock
        ;;
    check) run_target check "${2:-all}" ;;
    test) run_target test "${2:-all}" ;;
    eval-prompts) shift; compose run --rm --no-deps backend uv run python -m mato_loop_backend.prompt_eval "$@" ;;
    help|-h|--help|'') usage ;;
    *) usage >&2; exit 1 ;;
esac
