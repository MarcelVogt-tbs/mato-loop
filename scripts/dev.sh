#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MATO_LOOP_DEV_REPOSITORY="$(git -C "$PROJECT_ROOT" rev-parse --path-format=absolute --git-common-dir)"
export MATO_LOOP_DEV_REPOSITORY
export MATO_LOOP_DEV_CACHE_PREFIX="mato-loop-cache-$(printf '%s' "$MATO_LOOP_DEV_REPOSITORY" | sha256sum | cut -c1-12)"

resource_ids() {
    docker "$1" ls -q --filter "label=com.docker.compose.project=$2" "${@:3}"
}

verify_resources() {
    local kind id path repository cache labels ids worktree="${2:-$PROJECT_ROOT}"
    for kind in container volume; do
        if [[ "$kind" == container ]]; then ids="$(resource_ids "$kind" "$1" -a)"; else ids="$(resource_ids "$kind" "$1")"; fi
        for id in $ids; do
            labels='.Labels'
            [[ "$kind" != container ]] || labels='.Config.Labels'
            path="$(docker "$kind" inspect --format "{{index $labels \"matoloop.dev.worktree\"}}" "$id")"
            if [[ "$kind" == container && ( -z "$path" || "$path" == '<no value>' ) ]]; then
                path="$(docker container inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$id")"
            fi
            repository="$(docker "$kind" inspect --format "{{index $labels \"matoloop.dev.repository\"}}" "$id")"
            cache="$(docker "$kind" inspect --format "{{index $labels \"matoloop.dev.cache\"}}" "$id")"
            [[ "$path" == "$worktree" && ( "$repository" == "$MATO_LOOP_DEV_REPOSITORY" || ( "$worktree" == "$PROJECT_ROOT" && ( -z "$repository" || "$repository" == '<no value>' ) ) ) && ( -z "$cache" || "$cache" == '<no value>' ) ]] || {
                echo "Foreign or unverified $kind: $id. No changes made." >&2; return 1;
            }
        done
    done
}

prepare_caches() {
    local name="$MATO_LOOP_DEV_CACHE_PREFIX-downloads-v1" labels
    [[ " $1 " == *" $name "* ]] || return 0
    if ! docker volume inspect "$name" >/dev/null 2>&1; then
        [[ "$2" == prepare ]] || return 0
        docker volume create --label "matoloop.dev.repository=$MATO_LOOP_DEV_REPOSITORY" --label matoloop.dev.cache=downloads "$name" >/dev/null
    fi
    labels="$(docker volume inspect --format '{{index .Labels "matoloop.dev.repository"}}|{{index .Labels "matoloop.dev.cache"}}|{{index .Labels "matoloop.dev.worktree"}}' "$name")"
    [[ "$labels" == "$MATO_LOOP_DEV_REPOSITORY|downloads|" || "$labels" == "$MATO_LOOP_DEV_REPOSITORY|downloads|<no value>" ]] || {
        echo "Foreign or unverified cache: $name. No changes made." >&2; return 1;
    }
}

cleanup() {
    local project='' mode='' yes=false id kind path before after
    while (( $# )); do
        case "$1" in
            --project) project="${2:?Missing project name}"; shift ;;
            --dependencies|--all) [[ -z "$mode" ]] || return 1; mode="$1" ;;
            --yes) yes=true ;;
            *) usage >&2; return 1 ;;
        esac
        shift
    done
    if [[ -z "$project" ]]; then
        [[ -z "$mode" && "$yes" == false ]] || { echo 'Deletion requires --project and a mode.' >&2; return 1; }
        docker compose ls --all
        docker volume ls --filter label=matoloop.dev.worktree --format '{{.Name}}\t{{.Label "matoloop.dev.worktree"}}'
        return
    fi
    local containers volumes networks
    containers="$(resource_ids container "$project" -a)"
    volumes="$(resource_ids volume "$project")"
    networks="$(resource_ids network "$project")"
    [[ -n "$containers$volumes$networks" ]] || { echo 'No stack resources found.' >&2; return 1; }
    printf 'Stack: %s\nContainers: %s\nVolumes: %s\nNetworks: %s\n' "$project" "$containers" "$volumes" "$networks"
    [[ -n "$mode" ]] || { [[ "$yes" == false ]]; return; }
    local verified_networks=''
    for id in $networks; do
        kind="$(docker network inspect --format '{{index .Labels "matoloop.dev.repository"}}' "$id")"
        path="$(docker network inspect --format '{{index .Labels "matoloop.dev.worktree"}}' "$id")"
        if [[ "$kind" == "$MATO_LOOP_DEV_REPOSITORY" && "$path" == /* ]]; then verified_networks+="$id ";
        else printf 'Retaining unverified network: %s\n' "$id"; fi
    done
    networks="$verified_networks"
    path="$PROJECT_ROOT"
    for id in $volumes; do
        path="$(docker volume inspect --format '{{index .Labels "matoloop.dev.worktree"}}' "$id")"; break
    done
    if [[ -z "$volumes" ]]; then
        for id in $networks; do path="$(docker network inspect --format '{{index .Labels "matoloop.dev.worktree"}}' "$id")"; break; done
        for id in $containers; do path="$(docker container inspect --format '{{index .Config.Labels "matoloop.dev.worktree"}}' "$id")"; break; done
    fi
    [[ "$path" == /* ]] || { echo 'Missing worktree ownership.' >&2; return 1; }
    verify_resources "$project" "$path"
    for id in $networks; do
        [[ "$(docker network inspect --format '{{index .Labels "matoloop.dev.worktree"}}' "$id")" == "$path" ]] || { echo 'Foreign network worktree.' >&2; return 1; }
    done
    [[ -z "$(resource_ids container "$project" --filter status=running)" ]] || { echo 'Stop the stack explicitly before cleanup.' >&2; return 1; }
    if [[ "$mode" == --dependencies ]]; then
        local selected=''
        for id in $volumes; do
            kind="$(docker volume inspect --format '{{index .Labels "com.docker.compose.volume"}}' "$id")"
            case "$kind" in dev_data|backend_uv_data|frontend_node_modules|frontend_yarn_cache|embedding_cache|matoloop_agent_dependency_data|shell_decider_eval_cache) selected+="$id " ;; esac
        done
        volumes="$selected"; networks=''
    fi
    [[ -n "$containers$volumes$networks" ]] || { echo 'No selected resources to remove.'; return; }
    printf 'Delete stopped containers and selected volumes: %s\n' "$volumes"
    [[ "$mode" != --all ]] || echo 'WARNING: This deletes accounts, database, secrets, files, Agent state, pairing and workspace.'
    before="$(docker inspect $containers $volumes $networks)"
    if [[ "$yes" == false ]]; then
        [[ -t 0 ]] || { echo 'Explicit non-interactive deletion requires --yes.' >&2; return 1; }
        read -r -p "Type $project to confirm deletion: " id
        [[ "$id" == "$project" ]] || return 1
    fi
    verify_resources "$project" "$path"
    after="$(docker inspect $containers $volumes $networks)"
    [[ "$before" == "$after" ]] || { echo 'Stack changed; inspect it again.' >&2; return 1; }
    # Never force: Docker rejects running containers, attached networks and volumes still in use.
    for id in $containers; do docker container rm "$id"; done
    for id in $volumes; do docker volume rm "$id"; done
    for id in $networks; do docker network rm "$id"; done
}

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
    project="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])' <<< "$config")"
    verify_resources "$project"
    local key name kind external path names='' prepare=check
    while IFS='|' read -r key name external; do
        if [[ "$key" == download_cache ]]; then
            [[ "$external" == True && "$name" == "$MATO_LOOP_DEV_CACHE_PREFIX-downloads-v1" ]] || return 1
            names+=" $name "
        elif docker volume inspect "$name" >/dev/null 2>&1; then
            path="$(docker volume inspect --format '{{index .Labels "matoloop.dev.worktree"}}' "$name")"
            [[ "$path" == "$PROJECT_ROOT" ]] || { echo "Foreign or unverified volume: $name" >&2; return 1; }
            if [[ "$key" == app_data && ( "${1:-}" == up || "${1:-}" == run || "${1:-}" == create || "${1:-}" == start || "${1:-}" == restart ) ]]; then
                [[ "$(docker volume inspect --format '{{index .Labels "matoloop.dev.layout"}}' "$name")" == consolidated-v1 ]] || {
                    echo 'Legacy app_data layout. Keep its stack unchanged; back up and migrate it or use a new stack name. No data was moved or deleted.' >&2; return 1;
                }
            fi
        fi
    done < <(python3 -c 'import json,sys; c=json.load(sys.stdin); [print(k,v["name"],v.get("external",False),sep="|") for k,v in c.get("volumes",{}).items()]' <<< "$config")
    case "${1:-}" in up|run|create) prepare=prepare ;; esac
    prepare_caches "$names" "$prepare"
    if [[ "$prepare" == prepare ]]; then
        # Subpaths must exist before Docker creates the service mounts. This service has no source mount.
        "${command[@]}" --profile storage run --rm --no-deps storage-init
    fi
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
        printf '%s\n' 'This deletes instance volumes, including accounts, secrets, Agent state, pairing and Agent workspace. Shared download caches remain.' >&2
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
        "  cleanup             List Compose stacks and labeled worktree volumes (read-only)" \
        "  cleanup --project NAME [--dependencies|--all] [--yes] Preview or delete stopped stack resources" \
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
    compose run --rm --no-deps \
        --volume /workspace/apps/frontend/node_modules \
        --volume /workspace/apps/frontend/.quasar \
        --volume /workspace/apps/frontend/dist \
        --volume /workspace/apps/frontend/.yarn frontend sh -lc "$1"
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
    local version command
    version="$(python3 libs/python/version/src/matoloop_version/__init__.py)"
    command="$(cat <<'SH'
set -eu
snapshot=$(mktemp -d /tmp/matoloop-agent-tests.XXXXXXXX)
trap 'rm -rf "$snapshot"' EXIT
python3 - "$snapshot" "$1" <<'PY'
import importlib.util
import shutil
import sys
from pathlib import Path

source = Path('/source')
snapshot = Path(sys.argv[1]) / 'source'
spec = importlib.util.spec_from_file_location('installer', source / 'scripts/matoloop-agent-installer.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
installer.copy_source(source, snapshot)
shutil.copytree(source / 'apps/matoloop-agent/tests', snapshot / 'apps/matoloop-agent/tests',
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
(snapshot / 'libs/python/version/src/matoloop_version/build-version.txt').write_text(sys.argv[2] + '\n')
PY
# Only the disposable code copy is made readable; the checkout remains untouched.
chmod -R a+rX "$snapshot"
cd "$snapshot/source/apps/matoloop-agent"
uv sync --frozen
uv run --no-sync python -m unittest discover -s tests -v
SH
)"
    run_matoloop_agent "set -- '$version'; $command"
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
    cleanup) shift; cleanup "$@" ;;
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
