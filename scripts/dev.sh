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
    cat <<EOF
Usage: $(basename "$0") <command> [options]

Development stack helper for MatoLoop.

Commands:
  up [options]          Start the development stack
  restart [options]     Recreate the development stack
  stop [service...]     Stop services without removing containers
  down                  Remove containers
  reset-storage         Remove containers and development volumes
  clean                 Remove containers, volumes, and local images
  logs [service...]     Follow logs
  status                Show service status
  config                Show the resolved Compose configuration
  doctor                Check pgvector and the embedding index
  db [args...]          Run Alembic
  lock <target>         Update a lockfile
  check [target]        Check backend, frontend, and execution agent
  test [target] [args]  Test a target; pass args to its test runner
  eval-prompts [args]   Run serial live prompt evaluations
  help                  Show this help

Options for up/restart:
  -d, --detach          Start in the background
  --build               Build images before starting

Check targets: all (default), backend, frontend, execution-agent
Test targets:  all (default), backend, frontend, execution-agent, rag
Lock targets:  backend, frontend, execution-agent

Examples:
  $(basename "$0") up --build -d
  $(basename "$0") restart --build
  $(basename "$0") check backend
  $(basename "$0") test backend tests/test_workspace_api.py -q
  $(basename "$0") test frontend tests/workspace.spec.js
  $(basename "$0") db revision --autogenerate -m "short description"
  $(basename "$0") config
EOF
}

invalid_option() {
    printf 'Unknown option: %s\n\n' "$1" >&2
    usage >&2
    exit 1
}

invalid_target() {
    printf 'Unknown target: %s\n\n' "$1" >&2
    usage >&2
    exit 1
}

parse_run_flags() {
    DETACH=0
    BUILD=0

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--detach) DETACH=1 ;;
            --build) BUILD=1 ;;
            *) invalid_option "$1" ;;
        esac
        shift
    done
}

compose_up() {
    local args=(up)
    [[ "$DETACH" -eq 0 ]] || args+=(-d)
    [[ "$BUILD" -eq 0 ]] || args+=(--build)
    compose "${args[@]}"
}

cmd_up() {
    parse_run_flags "$@"
    compose_up
}

run_backend() {
    local command="$1"
    shift
    compose run --rm --no-deps backend sh -lc "$command" sh "$@"
}

run_frontend() {
    local command="$1"
    shift
    compose run --rm --no-deps frontend sh -lc "$command" sh "$@"
}

run_execution_agent() {
    local command="$1"
    shift
    compose run --rm --no-deps --entrypoint sh execution-agent -lc "$command" sh "$@"
}

print_step() {
    printf '\n==> %s\n' "$1"
}

check_backend() {
    print_step "Check backend"
    run_backend "uv sync --frozen --group docs && uv run --no-sync mkdocs build --strict --config-file /workspace/mkdocs.yml --site-dir /tmp/mato-loop-docs && uv run python -m compileall src/mato_loop_backend && uv run ruff check src tests"
}

check_frontend() {
    print_step "Check frontend"
    run_frontend "yarn install --immutable && yarn lint && yarn build"
}

check_execution_agent() {
    print_step "Check execution agent"
    run_execution_agent "test \"\$(id -u)\" = 0 && command -v apt-get bwrap git openscad rg xauth xvfb-run >/dev/null && uv sync --frozen && uv run --no-sync python -m compileall src tests && uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests"
}

test_backend() {
    print_step "Test backend"
    run_backend 'uv sync --frozen && uv run pytest "$@"' "$@"
}

test_frontend() {
    print_step "Test frontend"
    run_frontend 'yarn install --immutable && yarn test "$@"' "$@"
}

test_execution_agent() {
    print_step "Test execution agent"
    if [[ $# -gt 0 ]]; then
        run_execution_agent 'uv sync --frozen && uv run --no-sync python -m unittest "$@"' "$@"
    else
        run_execution_agent "uv sync --frozen && uv run --no-sync python -m unittest discover -s tests -v"
    fi
}

test_rag() {
    print_step "Test local RAG stack"
    compose up -d --wait --wait-timeout 600 db embedding
    run_backend "uv sync --frozen && MATO_LOOP_RAG_INTEGRATION=1 uv run pytest tests/test_memory_rag_integration.py"
}

cmd_check() {
    if [[ $# -gt 1 ]]; then
        invalid_target "$2"
    fi

    case "${1:-all}" in
        all) check_backend; check_frontend; check_execution_agent ;;
        backend) check_backend ;;
        frontend) check_frontend ;;
        execution-agent) check_execution_agent ;;
        *) invalid_target "$1" ;;
    esac
}

cmd_test() {
    local target="${1:-all}"
    if [[ $# -gt 0 ]]; then
        shift
    fi

    case "$target" in
        all)
            [[ $# -eq 0 ]] || invalid_target "$1"
            test_backend
            test_frontend
            test_execution_agent
            ;;
        backend) test_backend "$@" ;;
        frontend) test_frontend "$@" ;;
        execution-agent) test_execution_agent "$@" ;;
        rag)
            [[ $# -eq 0 ]] || invalid_target "$1"
            test_rag
            ;;
        *) invalid_target "$target" ;;
    esac
}

cmd_db() {
    if [[ $# -eq 0 ]]; then
        compose run --rm backend uv run alembic --help
        return
    fi

    compose run --rm backend uv run alembic "$@"
    if [[ "$1" == "revision" ]]; then
        compose run --rm --no-deps --workdir /workspace backend \
            chown -R "$(id -u):$(id -g)" /workspace/apps/backend/alembic/versions
    fi
}

cmd_lock() {
    if [[ $# -ne 1 ]]; then
        invalid_target "${2:-${1:-}}"
    fi

    case "$1" in
        backend) compose run --rm --no-deps backend uv lock ;;
        frontend) run_frontend "yarn install --mode=update-lockfile" ;;
        execution-agent) run_execution_agent "uv lock" ;;
        *) invalid_target "$1" ;;
    esac
}

cmd_restart() {
    parse_run_flags "$@"
    compose down
    compose_up
}

cmd_doctor() {
    compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extversion AS vector_version FROM pg_extension WHERE extname = '\''vector'\'';"'
    compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT format_type(a.atttypid, a.atttypmod) AS embedding_type FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = '\''memory_embeddings'\'' AND a.attname = '\''embedding'\'';"'
    compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT format_type(a.atttypid, a.atttypmod) AS episode_embedding_type FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = '\''message_search_chunks'\'' AND a.attname = '\''embedding'\'';"'
    compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) AS memory_count FROM memories;"'
    compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT status, count(*) FROM memory_embeddings GROUP BY status ORDER BY status;"'
    compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) AS episode_chunk_count FROM message_search_chunks;"'
    compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT status, count(*) FROM message_search_chunks GROUP BY status ORDER BY status;"'
    compose exec -T embedding python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:7997/health', timeout=5).read().decode())"
}

cd "$PROJECT_ROOT"

case "${1:-}" in
    up) shift; cmd_up "$@" ;;
    restart) shift; cmd_restart "$@" ;;
    stop) shift; compose stop "$@" ;;
    down) compose down ;;
    reset-storage) compose down -v ;;
    clean) compose down -v --rmi local ;;
    logs) shift; compose logs -f "$@" ;;
    status) compose ps ;;
    config) compose config ;;
    doctor) cmd_doctor ;;
    db) shift; cmd_db "$@" ;;
    lock) shift; cmd_lock "$@" ;;
    check) shift; cmd_check "$@" ;;
    test) shift; cmd_test "$@" ;;
    eval-prompts) shift; compose run --rm --no-deps backend uv run python -m mato_loop_backend.prompt_eval "$@" ;;
    help|-h|--help|'') usage ;;
    *) printf 'Unknown command: %s\n\n' "$1" >&2; usage >&2; exit 1 ;;
esac
