#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

compose() {
    export MATO_LOOP_DEV_WORKTREE="$PROJECT_ROOT"
    export MATO_LOOP_DEV_PROJECT_NAME="mato-loop-$(printf '%s' "$PROJECT_ROOT" | sha256sum | cut -c1-12)"
    local command=(docker compose --env-file .env.dev)
    if [[ -f .env.dev.override ]]; then command+=(--env-file .env.dev.override); fi
    command+=(-f docker-compose.dev.yaml)
    # Only overlays/profiles precede the subcommand; Compose handles service arguments.
    while [[ "${1:-}" == -f || "${1:-}" == --profile ]]; do
        command+=("$1" "${2:?Missing Compose option value}"); shift 2
    done
    [[ "${1:-}" != -* ]] || { echo 'Use .env.dev.override for instance settings.' >&2; return 1; }
    local config arg project answer deleting=false
    config="$("${command[@]}" config --format json)"
    python3 "$PROJECT_ROOT/scripts/dev-instance.py" "$PROJECT_ROOT" <<< "$config"
    if [[ "${1:-}" == down ]]; then
        for arg in "${@:2}"; do
            if [[ "$arg" == --volumes* || ( "$arg" == -[^-]* && "$arg" == *v* ) ]]; then
                deleting=true
                break
            fi
        done
    fi
    if [[ "$deleting" == true ]]; then
        printf 'Worktree: %s\n' "$PROJECT_ROOT" >&2
        printf '%s\n' 'This deletes named volumes, including accounts, secrets, Agent state, pairing and Agent workspace.' >&2
        if [[ "${RESET_CONFIRMED:-false}" != true ]]; then
            project="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])' <<< "$config")"
            [[ -t 0 ]] || { echo 'Use reset-storage --yes for explicit non-interactive deletion.' >&2; return 1; }
            read -r -p "Type $project to delete its volumes: " answer
            [[ "$answer" == "$project" ]] || return 1
        fi
    fi
    "${command[@]}" "$@"
    if [[ "${1:-}" == up || "${1:-}" == ps ]]; then
        printf 'Worktree: %s\n' "$PROJECT_ROOT" >&2
        local service binding port
        for service in frontend backend; do
            port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["services"][sys.argv[1]]["ports"][0]["target"])' "$service" <<< "$config")"
            binding="$("${command[@]}" port "$service" "$port" 2>/dev/null)" || continue
            printf '%s: http://localhost:%s\n' "$service" "${binding##*:}" >&2
        done
    fi
}

usage() {
    printf '%s\n' \
        "Usage: ./scripts/dev.sh <command> [args]" \
        "" \
        "Commands:" \
        "  up [--pwa] [--build] [-d] Start detached and wait for readiness" \
        "  stop [service]      Stop services" \
        "  down                Remove containers" \
        "  reset-storage [--yes] Delete named volumes, including Agent state, after confirmation" \
        "  logs [service]      Show logs" \
        "  status              Show worktree, URLs, and service status" \
        "  config              Show the resolved Compose configuration" \
        "  compose [args]      Run Docker Compose with development configuration" \
        "  doctor              Check instance ownership, readiness, pgvector and embeddings" \
        "  db [args]           Run Alembic" \
        "  lock <target>       Update the backend, frontend, or MatoLoop Agent lockfile" \
        "  format <target>     Format backend or MatoLoop Agent code" \
        "  check [target]      Check backend, frontend, and MatoLoop Agent" \
        "  test [target]       Test backend, frontend, MatoLoop Agent, or local RAG stack" \
        "" \
        "Evaluations: ./scripts/eval.sh help"
}

run_backend() {
    compose run --rm --no-deps --env UV_LINK_MODE=copy --volume /opt/mato-loop/backend/venv backend sh -lc "$1"
}

run_frontend() {
    compose run --rm --no-deps --volume /workspace/apps/frontend/node_modules frontend sh -lc "$1"
}

run_matoloop_agent() {
    compose run --rm --no-deps --env UV_LINK_MODE=copy --volume /opt/matoloop-agent/venv --entrypoint sh matoloop-agent -lc "$1"
}

check_backend() {
    run_backend "uv sync --frozen --group docs && uv run --no-sync mkdocs build --strict --config-file /workspace/mkdocs.yml --site-dir /tmp/mato-loop-docs && uv run python -m compileall src/mato_loop_backend && uv run ruff check src tests /workspace/libs/python/version/src"
}

check_frontend() {
    run_frontend "yarn install --immutable && yarn lint && yarn build"
}

check_matoloop_agent() {
    run_matoloop_agent "test \"\$(id -u)\" = 0 && command -v apt-get git openscad rg xauth xvfb-run >/dev/null && uv sync --frozen && uv run --no-sync python -m compileall src tests && uv run --no-sync ruff check src tests /source/scripts/matoloop-agent-installer.py && uv run --no-sync ruff format --check src tests /source/scripts/matoloop-agent-installer.py"
}

test_backend() {
    local args=""
    if (( $# )); then printf -v args ' %q' "$@"; fi
    run_backend "uv sync --frozen && uv run pytest$args"
}

test_frontend() {
    run_frontend "yarn install --immutable && yarn test"
}

test_matoloop_agent() {
    run_matoloop_agent "uv sync --frozen && uv run --no-sync python -m unittest discover -s tests -v"
}

test_rag() {
    compose up -d --wait --wait-timeout 600 db embedding
    run_backend "uv sync --frozen && MATO_LOOP_RAG_INTEGRATION=1 uv run pytest tests/test_memory_rag_integration.py"
}

format_backend() {
    run_backend "uv sync --frozen && uv run ruff check --fix src tests"
}

format_matoloop_agent() {
    run_matoloop_agent "uv sync --frozen && uv run --no-sync ruff format src tests /source/scripts/matoloop-agent-installer.py"
}

run_target() {
    local operation="$1"
    local target="${2:-all}"
    case "$operation:$target" in
        check:all) check_backend; check_frontend; check_matoloop_agent ;;
        check:backend) check_backend ;;
        check:frontend) check_frontend ;;
        check:matoloop-agent) check_matoloop_agent ;;
        test:all) test_backend; test_frontend; test_matoloop_agent ;;
        test:backend) test_backend "${@:3}" ;;
        test:frontend) test_frontend ;;
        test:matoloop-agent) test_matoloop_agent ;;
        test:rag) test_rag ;;
        *) printf 'Unbekanntes Target: %s\n' "$target" >&2; exit 1 ;;
    esac
}

cd "$PROJECT_ROOT"

case "${1:-}" in
    up)
        shift
        up_args=()
        pwa=false
        for arg in "$@"; do
            if [[ "$arg" == "--pwa" ]]; then
                pwa=true
            else
                up_args+=("$arg")
            fi
        done
        if [[ "$pwa" == true ]]; then
            export MATO_LOOP_FRONTEND_MODE=pwa
        fi
        compose up --wait --wait-timeout 900 "${up_args[@]}"
        ;;
    stop) shift; compose stop "$@" ;;
    down) shift; compose down "$@" ;;
    reset-storage)
        [[ $# == 1 || ( $# == 2 && "$2" == --yes ) ]] || { usage >&2; exit 1; }
        RESET_CONFIRMED=false
        if [[ "${2:-}" == --yes ]]; then RESET_CONFIRMED=true; fi
        compose down --volumes
        ;;
    logs) shift; compose logs -f "$@" ;;
    status) compose ps ;;
    config) shift; compose config "$@" ;;
    compose) shift; compose "$@" ;;
    doctor)
        compose ps --all
        compose exec -T backend sh -lc 'curl -fsS "http://localhost:$MATO_LOOP_BIND_BACKEND_PORT/api/health"'
        compose exec -T frontend node -e 'fetch("http://localhost:" + process.env.MATO_LOOP_BIND_FRONTEND_PORT).then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))'
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extversion AS vector_version FROM pg_extension WHERE extname = '\''vector'\'';"'
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT format_type(a.atttypid, a.atttypmod) AS embedding_type FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = '\''memory_embeddings'\'' AND a.attname = '\''embedding'\'';"'
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT format_type(a.atttypid, a.atttypmod) AS episode_embedding_type FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = '\''message_search_chunks'\'' AND a.attname = '\''embedding'\'';"'
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) AS memory_count FROM memories;"'
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT status, count(*) FROM memory_embeddings GROUP BY status ORDER BY status;"'
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) AS episode_chunk_count FROM message_search_chunks;"'
        compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT status, count(*) FROM message_search_chunks GROUP BY status ORDER BY status;"'
        compose exec -T embedding python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:7997/health', timeout=5).read().decode())"
        ;;
    db)
        shift
        compose run --rm backend uv run alembic "$@"
        if [[ "${1:-}" == "revision" ]]; then
            compose run --rm --no-deps --workdir /workspace backend \
                chown -R "$(id -u):$(id -g)" /workspace/apps/backend/alembic/versions
        fi
        ;;
    lock)
        case "${2:-}" in
            backend) compose run --rm --no-deps backend uv lock ;;
            frontend) run_frontend "yarn install --mode=update-lockfile" ;;
            matoloop-agent) run_matoloop_agent "uv lock" ;;
            *) printf 'Unbekanntes Lock-Target: %s\n' "${2:-}" >&2; exit 1 ;;
        esac
        ;;
    format)
        case "${2:-}" in
            backend) format_backend ;;
            matoloop-agent) format_matoloop_agent ;;
            *) printf 'Unbekanntes Format-Target: %s\n' "${2:-}" >&2; exit 1 ;;
        esac
        ;;
    check) run_target check "${2:-all}" ;;
    test) run_target test "${2:-all}" "${@:3}" ;;
    help|-h|--help|'') usage ;;
    *) usage >&2; exit 1 ;;
esac
