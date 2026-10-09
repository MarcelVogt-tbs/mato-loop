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
SERVICE=/etc/systemd/system/matoloop-agent.service
LEGACY_SERVICE=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/matoloop-agent.service
ROOT=
if [ -f "$RESOLVED_SCRIPT" ]; then
    ROOT=$(CDPATH= cd -- "$(dirname -- "$RESOLVED_SCRIPT")/.." && pwd)
fi
INPUT=
if [ -t 0 ]; then
    INPUT=/dev/stdin
elif (: </dev/tty) 2>/dev/null; then
    INPUT=/dev/tty
fi

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

if [ "$INSTALLED" = true ] && [ -x "$PYTHON" ] && [ -f "$MANAGER" ]; then
    case "${1:-run}" in
        install|update|status|uninstall) exec "$PYTHON" "$MANAGER" "$@" ;;
        -h|--help)
            "$INSTALL_ROOT/venv/bin/matoloop-agent" --help
            printf '%s\n' \
                '' \
                'native installation commands:' \
                '  install [--home PATH] [--instance-url URL]' \
                '  update' \
                '  status' \
                '  uninstall [--purge] [--yes]' \
                '' \
                "Run 'matoloop-agent COMMAND --help' for command-specific options."
            exit 0
            ;;
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
    { [ -e "$WRAPPER" ] || [ -L "$WRAPPER" ] || [ -e "$SERVICE" ] || [ -L "$SERVICE" ] ||
      [ -e "$LEGACY_SERVICE" ] || [ -L "$LEGACY_SERVICE" ]; }; then
    fail "An unmanaged MatoLoop Agent command or service already exists."
fi
if [ -d "$INSTALL_ROOT" ] && [ ! -f "$INSTALL_ROOT/.managed-by-matoloop-agent" ] &&
    [ -n "$(ls -A "$INSTALL_ROOT")" ]; then
    fail "The installation root is not empty and is not managed by MatoLoop."
fi
mkdir -p "$TOOLS_DIR/bin" "$TOOLS_DIR/python" "$INSTALL_ROOT/cache/uv"
chmod 700 "$INSTALL_ROOT" "$TOOLS_DIR"
: > "$INSTALL_ROOT/.managed-by-matoloop-agent"
chmod 600 "$INSTALL_ROOT/.managed-by-matoloop-agent"

MISSING_PACKAGES=
command -v curl >/dev/null 2>&1 || MISSING_PACKAGES=curl
if ! command -v git >/dev/null 2>&1; then
    MISSING_PACKAGES="${MISSING_PACKAGES:+$MISSING_PACKAGES }git"
fi
if [ -n "$MISSING_PACKAGES" ]; then
    printf 'Missing system packages: %s\n' "$MISSING_PACKAGES"
    answer=n
    if [ -n "$INPUT" ]; then
        printf '%s' 'Install missing system dependencies? [Y/n] '
        read -r answer < "$INPUT"
        answer=${answer:-y}
    fi
    case "$answer" in
        y|Y|yes|YES|Yes)
            command -v apt-get >/dev/null 2>&1 && command -v dpkg-query >/dev/null 2>&1 ||
                fail "Automatic system dependency installation requires Debian or Ubuntu."
            command -v sudo >/dev/null 2>&1 || fail "sudo is required to install system dependencies."
            [ ! -L "$INSTALL_ROOT/system-packages.txt" ] || fail "Invalid system package tracking file."
            sudo apt-get update
            # shellcheck disable=SC2086
            sudo apt-get install -y --no-install-recommends $MISSING_PACKAGES
            for package in $MISSING_PACKAGES; do
                printf '%s\n' "$package" >> "$INSTALL_ROOT/system-packages.txt"
            done
            chmod 600 "$INSTALL_ROOT/system-packages.txt"
            ;;
        *) fail "$MISSING_PACKAGES is required to bootstrap the private runtime." ;;
    esac
fi

if [ ! -x "$UV" ]; then
    printf 'Installing private uv %s...\n' "$UV_VERSION"
    curl --proto '=https' --tlsv1.2 -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" |
        env PATH="$TOOLS_DIR/bin:$PATH" UV_UNMANAGED_INSTALL="$TOOLS_DIR/bin" sh
fi

export UV_CACHE_DIR="$INSTALL_ROOT/cache/uv"
export UV_PYTHON_INSTALL_DIR="$TOOLS_DIR/python"
export UV_PYTHON_INSTALL_BIN=0
export UV_MANAGED_PYTHON=1
"$UV" python install 3.13
BOOTSTRAP_PYTHON=$("$UV" python find 3.13)

if [ -n "$ROOT" ] && [ -f "$ROOT/apps/matoloop-agent/pyproject.toml" ]; then
    if [ -n "$INPUT" ]; then
        exec "$BOOTSTRAP_PYTHON" "$ROOT/scripts/matoloop-agent-installer.py" install --source "$ROOT" "$@" < "$INPUT"
    fi
    exec "$BOOTSTRAP_PYTHON" "$ROOT/scripts/matoloop-agent-installer.py" install --source "$ROOT" "$@"
fi

INSTANCE_URL=
HOME_DIRECTORY=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --instance-url) [ "$#" -ge 2 ] || fail "--instance-url requires a URL."; INSTANCE_URL=$2; shift 2 ;;
        --home) [ "$#" -ge 2 ] || fail "--home requires a path."; HOME_DIRECTORY=$2; shift 2 ;;
        *) fail "Unknown installation option: $1" ;;
    esac
done
if [ -z "$INSTANCE_URL" ] && [ -n "$INPUT" ]; then
    printf '%s' 'MatoLoop instance URL (https://host): '
    read -r INSTANCE_URL < "$INPUT"
fi
INSTANCE_URL=${INSTANCE_URL%/}
case "$INSTANCE_URL" in
    http://*|https://*) ;;
    *) fail "A MatoLoop instance URL is required." ;;
esac
REVISION=$(curl --fail --silent --show-error --max-time 30 --proto '=https,http' \
    -- "$INSTANCE_URL/api/execution-agents/installation-target" | "$BOOTSTRAP_PYTHON" -c '
import json, re, sys
try:
    target = json.loads(sys.stdin.read(4097))
    revision = target["revision"]
    version = target["version"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid revision")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-" + revision[:8], version):
        raise ValueError("Invalid version")
except (ValueError, KeyError, TypeError):
    raise SystemExit("The backend has no reproducible Agent installation target.")
print(revision)
')
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/matoloop-agent-source.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT HUP INT TERM
git init --quiet "$STAGE/source"
git -C "$STAGE/source" remote add -- origin "${MATOLOOP_AGENT_SOURCE_URL:-https://git.devmato.net/devmato/MatoLoop.git}"
git -C "$STAGE/source" fetch --depth=1 --no-tags origin "$REVISION"
[ "$(git -C "$STAGE/source" rev-parse 'FETCH_HEAD^{commit}')" = "$REVISION" ] || fail "Source does not match the backend commit."
git -C "$STAGE/source" checkout --detach "$REVISION"
[ -f "$STAGE/source/libs/python/version/src/matoloop_version/__init__.py" ] || fail "The target commit does not support versioned Agent installations."
set -- --source "$STAGE/source" --managed --instance-url "$INSTANCE_URL"
[ -z "$HOME_DIRECTORY" ] || set -- "$@" --home "$HOME_DIRECTORY"
if [ -n "$INPUT" ]; then
    "$BOOTSTRAP_PYTHON" "$STAGE/source/scripts/matoloop-agent-installer.py" install "$@" < "$INPUT"
else
    "$BOOTSTRAP_PYTHON" "$STAGE/source/scripts/matoloop-agent-installer.py" install "$@"
fi
