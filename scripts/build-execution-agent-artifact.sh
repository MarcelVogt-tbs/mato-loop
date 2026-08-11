#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${1:-develop-$(git -C "$ROOT" rev-parse --short=12 HEAD)}"
[[ "$LABEL" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || {
    printf 'Error: invalid artifact label: %s\n' "$LABEL" >&2
    exit 1
}
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || {
    printf 'Error: native artifacts currently require Linux amd64.\n' >&2
    exit 1
}
LIBC="$(getconf GNU_LIBC_VERSION 2>/dev/null)" || {
    printf 'Error: native artifacts currently require glibc.\n' >&2
    exit 1
}

NAME="mato-loop-execution-agent-linux-amd64-$LABEL"
OUTPUT_DIR="$ROOT/dist"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mato-loop-agent-build.XXXXXX")"
trap 'rm -rf "$TEMP_DIR"' EXIT

printf 'from mato_loop_execution_agent.__main__ import main\nmain()\n' > "$TEMP_DIR/entry.py"
cd "$ROOT/apps/execution-agent"
uv sync --frozen --group build
uv run --no-sync pyinstaller \
    --clean \
    --onefile \
    --name mato-loop-execution-agent \
    --copy-metadata mato-loop-execution-agent \
    --paths src \
    --distpath "$TEMP_DIR/dist" \
    --workpath "$TEMP_DIR/work" \
    --specpath "$TEMP_DIR/spec" \
    "$TEMP_DIR/entry.py"
"$TEMP_DIR/dist/mato-loop-execution-agent" --help >/dev/null

mkdir -p "$TEMP_DIR/$NAME" "$OUTPUT_DIR"
install -m 755 "$TEMP_DIR/dist/mato-loop-execution-agent" "$TEMP_DIR/$NAME/"
install -m 644 README.md "$TEMP_DIR/$NAME/"
install -m 644 agent.native.example.json "$TEMP_DIR/$NAME/agent.example.json"
printf 'version=%s\nrevision=%s\nplatform=linux-amd64\nlibc=%s\n' \
    "$(<"$ROOT/VERSION")" "$(git -C "$ROOT" rev-parse HEAD)" "$LIBC" > "$TEMP_DIR/$NAME/BUILD"
tar -C "$TEMP_DIR" -czf "$OUTPUT_DIR/$NAME.tar.gz" "$NAME"
(
    cd "$OUTPUT_DIR"
    sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256"
)
printf '%s\n' "$OUTPUT_DIR/$NAME.tar.gz"
