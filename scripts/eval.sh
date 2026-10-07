#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

compose() {
    "$PROJECT_ROOT/scripts/dev.sh" compose "$@"
}

usage() {
    printf '%s\n' \
        "Usage: ./scripts/eval.sh <target> [args]" \
        "" \
        "Targets:" \
        "  prompts              Run serial live prompt evaluations" \
        "  automation-permissions Compare automation permission prompts (20-minute limit)" \
        "  tool-contracts       Compare OpenCode names, reason fields, and permission behavior" \
        "  session-cache        Evaluate cache reuse across real sessions" \
        "  coding-comparison    Compare persisted coding-run artifacts" \
        "  browser-performance  Compare synthetic browser tool contracts (requires a running backend)" \
        "  shell-decider        Evaluate shell classification models, including Qwen and teacher experiments" \
        "" \
        "Use <target> --help for evaluator-specific options."
}

case "${1:-}" in
    tool-contracts) shift; compose run --rm --no-deps --env MATO_LOOP_EVAL_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse HEAD)" backend uv run python /workspace/scripts/tool-contracts-eval.py "$@" ;;
    automation-permissions) shift; compose run --rm --no-deps backend uv run python /workspace/scripts/automation-permissions-eval.py "$@" ;;
    prompts) shift; compose run --rm --no-deps backend uv run python -m mato_loop_backend.prompt_eval "$@" ;;
    session-cache) shift; compose run --rm --no-deps backend uv run python -m mato_loop_backend.session_cache_eval "$@" ;;
    coding-comparison) shift; compose run --rm --no-deps backend uv run python -m mato_loop_backend.coding_eval "$@" ;;
    browser-performance) shift; compose exec -T backend /opt/mato-loop/backend/venv/bin/python -B -m mato_loop_backend.browser_perf_eval "$@" ;;
    shell-decider) shift; compose -f docker-compose.eval.yaml run --build --rm --no-deps shell-decider-eval "$@" ;;
    help|-h|--help|'') usage ;;
    *) usage >&2; exit 1 ;;
esac
