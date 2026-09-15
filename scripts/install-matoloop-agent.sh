#!/usr/bin/env sh
set -eu

UV_VERSION=0.10.9
INSTALL_ROOT=${MATOLOOP_AGENT_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/matoloop-agent}
RESOLVED_SCRIPT=$(readlink -f "$0" 2>/dev/null || true)
INSTALLED=false
case "$RESOLVED_SCRIPT" in
    */bin/matoloop-agent)
        INSTALLED=true
        INSTALL_ROOT=$(dirname "$(dirname "$RESOLVED_SCRIPT")")
        export MATOLOOP_AGENT_WRAPPER_PATH="$0"
        ;;
esac
INSTALL_ROOT=$(readlink -m "$INSTALL_ROOT")
export MATOLOOP_AGENT_INSTALL_ROOT="$INSTALL_ROOT"
TOOLS_DIR="$INSTALL_ROOT/tools"
UV="$TOOLS_DIR/bin/uv"
PYTHON="$INSTALL_ROOT/bin/python"
MANAGER="$INSTALL_ROOT/installer.py"
WRAPPER=${XDG_BIN_HOME:-$HOME/.local/bin}/matoloop-agent
SERVICE=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/matoloop-agent.service
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." 2>/dev/null && pwd || true)

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

if [ "$INSTALLED" = true ] && [ -x "$PYTHON" ] && [ -f "$MANAGER" ]; then
    case "${1:-run}" in
        install|update|status|uninstall) exec "$PYTHON" "$MANAGER" "$@" ;;
        *)
            export MATOLOOP_AGENT_CONFIG="$INSTALL_ROOT/data/config.json"
            exec "$INSTALL_ROOT/venv/bin/matoloop-agent" "$@"
            ;;
    esac
fi

if [ -x "$PYTHON" ] && [ -f "$MANAGER" ]; then
    case "${1:-install}" in
        update|status|uninstall) exec "$PYTHON" "$MANAGER" "$@" ;;
    esac
fi

case "${1:-install}" in
    install) [ "$#" -eq 0 ] || shift ;;
    update|status|uninstall) fail "MatoLoop Agent is not installed." ;;
    -h|--help)
        printf '%s\n' 'Usage: install-matoloop-agent.sh [install] [--home PATH] [--instance-url URL]'
        exit 0
        ;;
    --*) ;;
    *) fail "unknown command: $1" ;;
esac

[ "$(uname -s)" = Linux ] || fail "Native installation is not supported on this platform yet."
[ "$(id -u)" -ne 0 ] || fail "The native Agent must be installed as a regular user."
[ "$INSTALL_ROOT" != / ] && [ "$INSTALL_ROOT" != "$HOME" ] && [ ! -L "$INSTALL_ROOT" ] ||
    fail "Unsafe managed installation root."
case "$HOME/" in
    "$INSTALL_ROOT/"*) fail "Unsafe managed installation root." ;;
esac
[ ! -e "$INSTALL_ROOT" ] || [ -d "$INSTALL_ROOT" ] || fail "The installation root is not a directory."
if [ ! -f "$INSTALL_ROOT/.managed-by-matoloop-agent" ] &&
    { [ -e "$WRAPPER" ] || [ -L "$WRAPPER" ] || [ -e "$SERVICE" ] || [ -L "$SERVICE" ]; }; then
    fail "An unmanaged MatoLoop Agent command or user service already exists."
fi
if [ -d "$INSTALL_ROOT" ] && [ ! -f "$INSTALL_ROOT/.managed-by-matoloop-agent" ] &&
    [ -n "$(ls -A "$INSTALL_ROOT")" ]; then
    fail "The installation root is not empty and is not managed by MatoLoop."
fi
command -v curl >/dev/null 2>&1 || fail "curl is required to bootstrap the private runtime."

mkdir -p "$TOOLS_DIR/bin" "$TOOLS_DIR/python" "$INSTALL_ROOT/cache/uv"
chmod 700 "$INSTALL_ROOT" "$TOOLS_DIR"
: > "$INSTALL_ROOT/.managed-by-matoloop-agent"
chmod 600 "$INSTALL_ROOT/.managed-by-matoloop-agent"

if [ ! -x "$UV" ]; then
    printf 'Installing private uv %s...\n' "$UV_VERSION"
    curl --proto '=https' --tlsv1.2 -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" |
        env UV_INSTALL_DIR="$TOOLS_DIR/bin" UV_NO_MODIFY_PATH=1 sh
fi

export UV_CACHE_DIR="$INSTALL_ROOT/cache/uv"
export UV_PYTHON_INSTALL_DIR="$TOOLS_DIR/python"
"$UV" python install 3.13
BOOTSTRAP_PYTHON=$("$UV" python find 3.13)

if [ -f "$ROOT/apps/matoloop-agent/pyproject.toml" ]; then
    exec "$BOOTSTRAP_PYTHON" "$ROOT/scripts/matoloop-agent-installer.py" install --source "$ROOT" "$@"
fi

command -v git >/dev/null 2>&1 || fail "git is required when installing outside a MatoLoop checkout."
BRANCH=${MATOLOOP_AGENT_BRANCH:-}
if [ -z "$BRANCH" ]; then
    BRANCH=main
    if [ -t 0 ]; then
        printf 'Git branch [%s]: ' "$BRANCH"
        read -r answer
        BRANCH=${answer:-$BRANCH}
    fi
fi
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/matoloop-agent-source.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT HUP INT TERM
git clone --branch "$BRANCH" --single-branch "${MATOLOOP_AGENT_SOURCE_URL:-https://git.devmato.net/devmato/MatoLoop.git}" "$STAGE/source"
"$BOOTSTRAP_PYTHON" "$STAGE/source/scripts/matoloop-agent-installer.py" install \
    --source "$STAGE/source" --managed "$@"
