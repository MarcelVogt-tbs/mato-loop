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
        "  up [--build] [-d]   Entwicklungsstack starten" \
        "  stop                Services stoppen" \
        "  down                Container entfernen" \
        "  reset-storage       Container und Volumes entfernen" \
        "  logs [service]      Logs anzeigen" \
        "  status              Service-Status anzeigen" \
        "  db [args]           Alembic ausfuehren" \
        "  check [target]      Backend und/oder Frontend pruefen" \
        "  test [target]       Backend und/oder Frontend testen"
}

run_backend() {
    compose run --rm --no-deps backend sh -lc "$1"
}

run_frontend() {
    compose run --rm --no-deps frontend sh -lc "$1"
}

check_backend() {
    run_backend "uv sync --frozen && uv run python -m compileall src/mato_loop_backend && uv run ruff check src tests"
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
        *) printf 'Unbekanntes Target: %s\n' "$target" >&2; exit 1 ;;
    esac
}

cd "$PROJECT_ROOT"

case "${1:-}" in
    up) shift; compose up "$@" ;;
    stop) compose stop ;;
    down) compose down ;;
    reset-storage) compose down -v ;;
    logs) shift; compose logs -f "$@" ;;
    status) compose ps ;;
    db) shift; compose run --rm backend uv run alembic "$@" ;;
    check) run_target check "${2:-all}" ;;
    test) run_target test "${2:-all}" ;;
    help|-h|--help|'') usage ;;
    *) usage >&2; exit 1 ;;
esac
