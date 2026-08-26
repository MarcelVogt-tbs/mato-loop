#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C
umask 077

detect_project_root() {
    local candidate="$1"
    local home="$2"
    if [[ -f "$candidate/apps/matoloop-agent/pyproject.toml" ]]; then
        printf '%s' "$candidate"
    else
        printf '%s' "$home/mato-loop-agent/source"
    fi
}

CHECKOUT_CANDIDATE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_SOURCE_DIR="$HOME/mato-loop-agent/source"
PROJECT_ROOT="$(detect_project_root "$CHECKOUT_CANDIDATE" "$HOME")"
AGENT_PROJECT="$PROJECT_ROOT/apps/matoloop-agent"
VENV="$AGENT_PROJECT/.venv"
DEFAULT_DATA_DIR="$HOME/mato-loop-agent/data"
DEFAULT_WORKSPACE_DIR="$HOME/mato-loop-agent/workspace"
DEFAULT_INSTANCE_URL="http://127.0.0.1:10020"
DEFAULT_BACKEND_URL="ws://127.0.0.1:10020/api/execution-agents/connect"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/matoloop-agent-installer"
STATE_FILE="$STATE_DIR/state"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="$SERVICE_DIR/matoloop-agent.service"
UV_VERSION="0.10.9"
INSTALL_USER="$(id -un)"
SOURCE_URL="https://git.devmato.net/devmato/MatoLoop.git"

DATA_DIR="$DEFAULT_DATA_DIR"
WORKSPACE_DIR="$DEFAULT_WORKSPACE_DIR"
INSTANCE_URL="$DEFAULT_INSTANCE_URL"
BACKEND_URL="$DEFAULT_BACKEND_URL"
BACKEND_DERIVED=true
SOURCE_ROOT="$PROJECT_ROOT"
VENV_CREATED=false
WORKSPACE_CREATED=false
LINGER_CREATED=false
SOURCE_CREATED=false
SELECTED_ACTION=""
ACTIVE_SOURCE_STAGE=""

cleanup_active_source_stage() {
    if [[ -n "$ACTIVE_SOURCE_STAGE" && -f "$ACTIVE_SOURCE_STAGE/.managed-by-matoloop-installer" ]]; then
        rm -rf -- "$ACTIVE_SOURCE_STAGE"
    fi
}

trap cleanup_active_source_stage EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

usage() {
    printf '%s\n' \
        "Usage: ./scripts/install-matoloop-agent.sh [command]" \
        "" \
        "Commands:" \
        "  install             Install from a checkout or managed clone" \
        "  repair              Repair or reconfigure the installation" \
        "  status              Show the user service status" \
        "  uninstall [--yes]   Remove the managed installation" \
        "  --self-test         Run side-effect-free script checks" \
        "" \
        "Without a command, an interactive menu is shown."
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

require_interactive() {
    [[ -t 0 && -t 1 ]] || die "Interactive input is required. Pass an explicit command instead."
}

confirm() {
    local prompt="$1"
    local default="${2:-yes}"
    local answer
    local suffix="[Y/n]"
    [[ "$default" == yes ]] || suffix="[y/N]"
    read -e -r -p "$prompt $suffix " answer
    answer="${answer:-$default}"
    [[ "$answer" =~ ^([yY]|[yY][eE][sS]|[jJ]|[jJ][aA])$ ]]
}

prompt_value() {
    local variable="$1"
    local prompt="$2"
    local default="$3"
    local response
    read -e -r -i "$default" -p "$prompt: " response
    printf -v "$variable" '%s' "${response:-$default}"
}

normalize_path() {
    local value="$1"
    case "$value" in
        '~') value="$HOME" ;;
        '~/'*) value="$HOME/${value#\~/}" ;;
    esac
    [[ "$value" == /* ]] || return 1
    realpath -m -- "$value"
}

is_same_or_parent() {
    local parent="${1%/}"
    local child="${2%/}"
    [[ "$child" == "$parent" || "$child" == "$parent/"* ]]
}

safe_managed_path() {
    local path="${1%/}"
    [[ -n "$path" && "$path" != / && "$path" != "$HOME" ]] || return 1
    is_same_or_parent "$path" "$PROJECT_ROOT" && return 1
    is_same_or_parent "$PROJECT_ROOT" "$path" && return 1
    is_same_or_parent "$path" "$SOURCE_ROOT" && return 1
    is_same_or_parent "$SOURCE_ROOT" "$path" && return 1
    is_same_or_parent "$path" "$HOME" && return 1
    return 0
}

validate_instance_url() {
    python3 - "$1" <<'PY'
import sys
from urllib.parse import urlsplit

try:
    value = urlsplit(sys.argv[1])
    valid = (
        value.scheme in {"http", "https"}
        and sys.argv[1].startswith(("http://", "https://"))
        and value.hostname is not None
        and value.username is None
        and value.password is None
        and value.query == ""
        and value.fragment == ""
        and "?" not in sys.argv[1]
        and "#" not in sys.argv[1]
        and not any(character.isspace() for character in sys.argv[1])
    )
    if value.port is not None and not 1 <= value.port <= 65535:
        valid = False
except ValueError:
    valid = False
raise SystemExit(0 if valid else 1)
PY
}

instance_to_backend_url() {
    local value="$1"
    while [[ "$value" == */ ]]; do
        value="${value%/}"
    done
    case "$value" in
        http://*) printf 'ws://%s/api/execution-agents/connect' "${value#http://}" ;;
        https://*) printf 'wss://%s/api/execution-agents/connect' "${value#https://}" ;;
        *) return 1 ;;
    esac
}

backend_to_instance_url() {
    local value="$1"
    case "$value" in
        ws://*) value="http://${value#ws://}" ;;
        wss://*) value="https://${value#wss://}" ;;
        *) return 1 ;;
    esac
    case "$value" in
        */api/execution-agents/connect) printf '%s' "${value%/api/execution-agents/connect}" ;;
        */execution-agents/connect) printf '%s' "${value%/execution-agents/connect}" ;;
        *) return 1 ;;
    esac
}

reset_state() {
    DATA_DIR="$DEFAULT_DATA_DIR"
    WORKSPACE_DIR="$DEFAULT_WORKSPACE_DIR"
    INSTANCE_URL="$DEFAULT_INSTANCE_URL"
    BACKEND_URL="$DEFAULT_BACKEND_URL"
    BACKEND_DERIVED=true
    SOURCE_ROOT="$PROJECT_ROOT"
    VENV_CREATED=false
    WORKSPACE_CREATED=false
    LINGER_CREATED=false
    SOURCE_CREATED=false
}

load_state() {
    local has_instance_url=false
    local has_backend_derived=false
    local line key value
    reset_state
    [[ -e "$STATE_FILE" ]] || return 0
    [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] || die "Installer state is not a regular file: $STATE_FILE"
    while IFS= read -r line || [[ -n "$line" ]]; do
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
            dataDir) DATA_DIR="$value" ;;
            workspaceDir) WORKSPACE_DIR="$value" ;;
            instanceUrl) INSTANCE_URL="$value"; has_instance_url=true ;;
            backendUrl) BACKEND_URL="$value" ;;
            backendDerived) BACKEND_DERIVED="$value"; has_backend_derived=true ;;
            sourceRoot) SOURCE_ROOT="$value" ;;
            venvCreated) VENV_CREATED="$value" ;;
            workspaceCreated) WORKSPACE_CREATED="$value" ;;
            lingerCreated) LINGER_CREATED="$value" ;;
            sourceCreated) SOURCE_CREATED="$value" ;;
        esac
    done < "$STATE_FILE"
    [[ "$VENV_CREATED" =~ ^(true|false)$ ]] || die "Invalid installer state."
    [[ "$WORKSPACE_CREATED" =~ ^(true|false)$ ]] || die "Invalid installer state."
    [[ "$LINGER_CREATED" =~ ^(true|false)$ ]] || die "Invalid installer state."
    [[ "$SOURCE_CREATED" =~ ^(true|false)$ ]] || die "Invalid installer state."
    if [[ "$has_instance_url" == false ]]; then
        INSTANCE_URL="$(backend_to_instance_url "$BACKEND_URL")" || die "Invalid backend URL in installer state."
        BACKEND_DERIVED=false
    fi
    [[ "$has_backend_derived" == false || "$BACKEND_DERIVED" =~ ^(true|false)$ ]] || die "Invalid installer state."
}

save_state() {
    local value temporary
    for value in "$DATA_DIR" "$WORKSPACE_DIR" "$INSTANCE_URL" "$BACKEND_URL" "$SOURCE_ROOT"; do
        [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || die "Paths and URLs must not contain line breaks."
    done
    mkdir -p "$STATE_DIR"
    chmod 700 "$STATE_DIR"
    temporary="$(mktemp "$STATE_DIR/.state.XXXXXX")"
    printf '%s\n' \
        "dataDir=$DATA_DIR" \
        "workspaceDir=$WORKSPACE_DIR" \
        "instanceUrl=$INSTANCE_URL" \
        "backendUrl=$BACKEND_URL" \
        "backendDerived=$BACKEND_DERIVED" \
        "sourceRoot=$SOURCE_ROOT" \
        "venvCreated=$VENV_CREATED" \
        "workspaceCreated=$WORKSPACE_CREATED" \
        "lingerCreated=$LINGER_CREATED" \
        "sourceCreated=$SOURCE_CREATED" > "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$STATE_FILE"
}

installation_state() {
    if [[ ! -e "$STATE_FILE" && ! -e "$SERVICE_FILE" ]]; then
        printf 'absent'
        return
    fi
    load_state
    if [[ -f "$DATA_DIR/config.json" && -x "$SOURCE_ROOT/apps/matoloop-agent/.venv/bin/matoloop-agent" && -f "$SERVICE_FILE" ]]; then
        printf 'installed'
    else
        printf 'partial'
    fi
}

choose_action() {
    local state choice
    require_interactive
    state="$(installation_state)"
    case "$state" in
        absent)
            printf 'No native MatoLoop Agent installation was found.\n'
            PS3="Selection: "
            select choice in "Install" "Exit"; do
                case "$REPLY" in
                    1) SELECTED_ACTION=install; return ;;
                    2) SELECTED_ACTION=exit; return ;;
                esac
            done
            ;;
        partial)
            printf 'An incomplete native MatoLoop Agent installation was found.\n'
            PS3="Selection: "
            select choice in "Repair or continue" "Remove remnants" "Exit"; do
                case "$REPLY" in
                    1) SELECTED_ACTION=repair; return ;;
                    2) SELECTED_ACTION=uninstall; return ;;
                    3) SELECTED_ACTION=exit; return ;;
                esac
            done
            ;;
        installed)
            printf 'A native MatoLoop Agent installation was found.\n'
            PS3="Selection: "
            select choice in "Repair or reconfigure" "Show status" "Uninstall" "Exit"; do
                case "$REPLY" in
                    1) SELECTED_ACTION=repair; return ;;
                    2) SELECTED_ACTION=status; return ;;
                    3) SELECTED_ACTION=uninstall; return ;;
                    4) SELECTED_ACTION=exit; return ;;
                esac
            done
            ;;
    esac
}

check_platform() {
    [[ "$(uname -s)" == Linux ]] || die "Native installation is supported only on Linux."
    command -v apt-get >/dev/null || die "This installer currently requires apt-get on Ubuntu or Debian."
    command -v systemctl >/dev/null || die "systemctl is required."
    command -v loginctl >/dev/null || die "loginctl is required."
    command -v sudo >/dev/null || die "sudo is required to install missing packages and enable linger."
}

install_dependencies() {
    local missing=()
    command -v git >/dev/null || missing+=(git)
    command -v curl >/dev/null || missing+=(curl)
    command -v rg >/dev/null || missing+=(ripgrep)
    command -v python3 >/dev/null || missing+=(python3)
    ((${#missing[@]} == 0)) && return
    require_interactive
    printf 'Missing Ubuntu packages: %s\n' "${missing[*]}"
    confirm "Install them with apt-get?" || die "Required packages were not installed."
    sudo env DEBIAN_FRONTEND=noninteractive dpkg --configure -a
    sudo env DEBIAN_FRONTEND=noninteractive apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
}

install_uv() {
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null && return
    require_interactive
    confirm "Install uv $UV_VERSION for the current user?" || die "uv is required."
    curl --proto '=https' --tlsv1.2 -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_NO_MODIFY_PATH=1 sh
    hash -r
    command -v uv >/dev/null || die "uv installation failed."
}

prompt_settings() {
    local answer existing_state=false original_instance_url
    load_state
    [[ -e "$STATE_FILE" ]] && existing_state=true
    if [[ "$existing_state" == true && "$SOURCE_ROOT" != "$PROJECT_ROOT" ]]; then
        die "This installation belongs to another checkout: $SOURCE_ROOT. Uninstall it before installing from this checkout."
    fi
    if [[ "$existing_state" == false ]]; then
        prompt_value answer "Agent data directory" "$DATA_DIR"
        DATA_DIR="$(normalize_path "$answer")" || die "The data directory must be absolute."
        prompt_value answer "Agent home and workspace" "$WORKSPACE_DIR"
        WORKSPACE_DIR="$(normalize_path "$answer")" || die "The workspace directory must be absolute."
    else
        printf 'Using managed data directory: %s\n' "$DATA_DIR"
        printf 'Using managed workspace: %s\n' "$WORKSPACE_DIR"
    fi
    if is_same_or_parent "$DATA_DIR" "$WORKSPACE_DIR" || is_same_or_parent "$WORKSPACE_DIR" "$DATA_DIR"; then
        die "Data and workspace directories must not overlap."
    fi
    safe_managed_path "$DATA_DIR" || die "Unsafe data directory: $DATA_DIR"

    if [[ ! -e "$DATA_DIR/agent-state/credential.json" ]]; then
        original_instance_url="$INSTANCE_URL"
        while true; do
            prompt_value INSTANCE_URL "MatoLoop instance URL" "$INSTANCE_URL"
            validate_instance_url "$INSTANCE_URL" && break
            printf 'Enter the normal http:// or https:// URL of the MatoLoop instance.\n' >&2
        done
        while [[ "$INSTANCE_URL" == */ ]]; do
            INSTANCE_URL="${INSTANCE_URL%/}"
        done
        if [[ "$BACKEND_DERIVED" == true || "$INSTANCE_URL" != "$original_instance_url" ]]; then
            BACKEND_URL="$(instance_to_backend_url "$INSTANCE_URL")"
            BACKEND_DERIVED=true
        fi
    else
        printf 'Keeping the paired MatoLoop instance URL: %s\n' "$INSTANCE_URL"
    fi
    SOURCE_ROOT="$PROJECT_ROOT"
}

prepare_managed_directories() {
    local entries=()
    if [[ ! -e "$STATE_FILE" && -d "$DATA_DIR" ]]; then
        shopt -s nullglob dotglob
        entries=("$DATA_DIR"/*)
        shopt -u nullglob dotglob
        ((${#entries[@]} == 0)) || die "The selected data directory is not empty."
    fi
    if [[ ! -d "$WORKSPACE_DIR" ]]; then
        WORKSPACE_CREATED=true
    fi
    save_state
    mkdir -p "$DATA_DIR" "$WORKSPACE_DIR"
    chmod 700 "$DATA_DIR"
    if [[ "$WORKSPACE_CREATED" == true ]]; then
        chmod 700 "$WORKSPACE_DIR"
    fi
    : > "$DATA_DIR/.managed-by-matoloop-installer"
}

prepare_source() {
    local parent stage
    if [[ -f "$AGENT_PROJECT/pyproject.toml" ]]; then
        if [[ -f "$PROJECT_ROOT/.git/matoloop-installer-managed" && "$SOURCE_CREATED" != true ]]; then
            SOURCE_CREATED=true
            save_state
        fi
        return
    fi
    [[ ! -e "$PROJECT_ROOT" ]] || die "The Agent source directory is incomplete: $PROJECT_ROOT"
    parent="$(dirname "$PROJECT_ROOT")"
    mkdir -p "$parent"
    shopt -s nullglob
    for stage in "$parent"/.source-clone.*; do
        if [[ -f "$stage/.managed-by-matoloop-installer" ]]; then
            rm -rf -- "$stage"
        fi
    done
    shopt -u nullglob
    ACTIVE_SOURCE_STAGE="$(mktemp -d "$parent/.source-clone.XXXXXX")"
    : > "$ACTIVE_SOURCE_STAGE/.managed-by-matoloop-installer"
    if ! git clone "$SOURCE_URL" "$ACTIVE_SOURCE_STAGE/repository"; then
        die "Could not clone the MatoLoop source."
    fi
    : > "$ACTIVE_SOURCE_STAGE/repository/.git/matoloop-installer-managed"
    mv -T -- "$ACTIVE_SOURCE_STAGE/repository" "$PROJECT_ROOT" || die "Could not publish the MatoLoop source."
    rm -rf -- "$ACTIVE_SOURCE_STAGE"
    ACTIVE_SOURCE_STAGE=""
    SOURCE_CREATED=true
    save_state
}

write_agent_config() {
    local git_executable shell_executable python_executable openscad_executable
    git_executable="$(command -v git || true)"
    shell_executable="$(command -v bash || true)"
    python_executable="$(command -v python3 || true)"
    openscad_executable="$(command -v openscad || true)"
    python3 - "$DATA_DIR/config.json" "$WORKSPACE_DIR" "$BACKEND_URL" \
        "$git_executable" "$shell_executable" "$python_executable" "$openscad_executable" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
workspace, backend_url, git, shell, python, openscad = sys.argv[2:]
if path.is_symlink():
    raise SystemExit("Refusing symlinked config.json.")
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
data["homeDirectory"] = workspace
data["allowInsecure"] = backend_url.startswith("ws://")
data.setdefault("allowNetwork", True)
if git:
    data["gitExecutable"] = git
else:
    data.pop("gitExecutable", None)
if shell:
    data["shellExecutable"] = shell
else:
    data.pop("shellExecutable", None)
process_executables = data.setdefault("processExecutables", {})
if python:
    process_executables["python"] = python
else:
    process_executables.pop("python", None)
if openscad:
    data["openscadExecutable"] = openscad
else:
    data.pop("openscadExecutable", None)
with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
    json.dump(data, temporary, indent=2)
    temporary.write("\n")
    temporary.flush()
    os.fsync(temporary.fileno())
    temporary_path = temporary.name
os.chmod(temporary_path, 0o600)
os.replace(temporary_path, path)
PY
}

systemd_quote() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//%/%%}"
    printf '"%s"' "$value"
}

write_service() {
    local temporary executable config
    mkdir -p "$SERVICE_DIR"
    executable="$(systemd_quote "$VENV/bin/matoloop-agent")"
    config="$(systemd_quote "$DATA_DIR/config.json")"
    temporary="$(mktemp "$SERVICE_DIR/.matoloop-agent.service.XXXXXX")"
    cat > "$temporary" <<EOF
[Unit]
Description=MatoLoop Computer Agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$executable --config $config run
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
EOF
    chmod 600 "$temporary"
    mv "$temporary" "$SERVICE_FILE"
}

enable_linger() {
    local linger
    linger="$(loginctl show-user "$INSTALL_USER" -p Linger --value)" || die "Could not determine the linger state for $INSTALL_USER."
    if [[ "$linger" != yes ]]; then
        sudo loginctl enable-linger "$INSTALL_USER"
        LINGER_CREATED=true
        save_state
    fi
}

install_or_repair() {
    local executable
    local sync_args=(--frozen --no-dev)
    require_interactive
    check_platform
    install_dependencies
    install_uv
    prompt_settings
    prepare_managed_directories
    prepare_source

    if [[ ! -d "$VENV" ]]; then
        VENV_CREATED=true
        save_state
    elif [[ "$VENV_CREATED" == false ]]; then
        sync_args+=(--inexact)
    fi
    uv python install 3.13
    uv --directory "$AGENT_PROJECT" sync "${sync_args[@]}"
    executable="$VENV/bin/matoloop-agent"
    [[ -x "$executable" ]] || die "The MatoLoop Agent executable was not installed."
    if [[ "$VENV_CREATED" == true ]]; then
        : > "$VENV/.managed-by-matoloop-installer"
    fi

    write_agent_config
    write_service
    enable_linger
    systemctl --user daemon-reload
    systemctl --user enable matoloop-agent.service
    systemctl --user restart matoloop-agent.service

    if [[ ! -e "$DATA_DIR/agent-state/credential.json" ]]; then
        printf '\nCreate a computer in MatoLoop under Settings > Computers, then enter its values.\n'
        if ! "$executable" --config "$DATA_DIR/config.json" --backend-url "$BACKEND_URL" pair; then
            printf 'The service is installed and waiting. Run this script with repair to retry pairing.\n' >&2
            return 1
        fi
    fi
    printf '\nMatoLoop Agent is running as %s.\n' "$INSTALL_USER"
    printf 'Status: systemctl --user status matoloop-agent\n'
    printf 'Logs:   journalctl --user -u matoloop-agent -f\n'
}

show_status() {
    load_state
    printf 'Source:    %s\nData:      %s\nWorkspace: %s\n' "$SOURCE_ROOT" "$DATA_DIR" "$WORKSPACE_DIR"
    systemctl --user status --no-pager matoloop-agent.service || true
}

remove_empty_parents() {
    rmdir "$HOME/mato-loop-agent" 2>/dev/null || true
    rmdir "$SERVICE_DIR/default.target.wants" 2>/dev/null || true
    rmdir "$SERVICE_DIR" 2>/dev/null || true
    rmdir "$(dirname "$SERVICE_DIR")" 2>/dev/null || true
}

uninstall_agent() {
    local force="${1:-false}"
    local remove_workspace=false
    local executable_venv source_parent stage
    local source_removed=false
    load_state
    [[ "$force" == true ]] || require_interactive
    if [[ "$force" != true ]]; then
        printf 'The service, credentials, cache, and managed agent data will be removed.\n'
        printf 'Data directory: %s\n' "$DATA_DIR"
        confirm "Continue?" no || { printf 'Cancelled.\n'; return; }
    fi
    if [[ "$force" != true && "$WORKSPACE_CREATED" == true && -d "$WORKSPACE_DIR" ]]; then
        if confirm "Also remove the installer-created workspace and all its files at $WORKSPACE_DIR?" no; then
            remove_workspace=true
        fi
    fi
    if [[ "$remove_workspace" == true ]] && ! safe_managed_path "$WORKSPACE_DIR"; then
        printf 'Refusing to remove unsafe workspace path: %s\n' "$WORKSPACE_DIR" >&2
        remove_workspace=false
    fi

    if systemctl --user show-environment >/dev/null 2>&1; then
        if systemctl --user is-active --quiet matoloop-agent.service; then
            systemctl --user stop matoloop-agent.service || die "The MatoLoop Agent service could not be stopped; no files were removed."
        fi
        systemctl --user disable matoloop-agent.service 2>/dev/null || true
    elif [[ -e "$SERVICE_FILE" ]]; then
        die "The user service manager is unavailable; no files were removed."
    fi
    rm -f -- "$SERVICE_FILE" "$SERVICE_DIR/default.target.wants/matoloop-agent.service" "$SERVICE_DIR"/.matoloop-agent.service.*
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user reset-failed matoloop-agent.service 2>/dev/null || true

    executable_venv="$SOURCE_ROOT/apps/matoloop-agent/.venv"
    if [[ "$VENV_CREATED" == true && -f "$executable_venv/.managed-by-matoloop-installer" ]]; then
        rm -rf -- "$executable_venv"
    fi
    if [[ -f "$DATA_DIR/.managed-by-matoloop-installer" ]] && safe_managed_path "$DATA_DIR"; then
        rm -rf -- "$DATA_DIR"
    fi
    if [[ "$remove_workspace" == true ]]; then
        rm -rf -- "$WORKSPACE_DIR"
    fi
    if [[ "$SOURCE_ROOT" == "$DEFAULT_SOURCE_DIR" && -f "$SOURCE_ROOT/.git/matoloop-installer-managed" ]]; then
        rm -rf -- "$SOURCE_ROOT"
        source_removed=true
    fi
    source_parent="$(dirname "$DEFAULT_SOURCE_DIR")"
    shopt -s nullglob
    for stage in "$source_parent"/.source-clone.*; do
        if [[ -f "$stage/.managed-by-matoloop-installer" ]]; then
            rm -rf -- "$stage"
        fi
    done
    shopt -u nullglob
    if [[ "$LINGER_CREATED" == true ]]; then
        sudo loginctl disable-linger "$INSTALL_USER"
    fi
    rm -rf -- "$STATE_DIR"
    remove_empty_parents
    printf 'The native MatoLoop Agent installation was removed.\n'
    [[ "$remove_workspace" == true || ! -d "$WORKSPACE_DIR" ]] || printf 'Workspace preserved: %s\n' "$WORKSPACE_DIR"
    if [[ "$source_removed" == true ]]; then
        printf 'The installer-created source clone was removed.\n'
    else
        printf 'The existing checkout was preserved.\n'
    fi
    printf 'uv and shared system packages were preserved.\n'
}

self_test() (
    local fixture prompted temporary
    temporary="$(mktemp -d)"
    trap 'rm -rf -- "$temporary"' EXIT
    HOME="$temporary/home"
    mkdir -p "$HOME"
    DEFAULT_DATA_DIR="$HOME/mato-loop-agent/data"
    DEFAULT_WORKSPACE_DIR="$HOME/mato-loop-agent/workspace"
    DEFAULT_SOURCE_DIR="$HOME/mato-loop-agent/source"
    DEFAULT_INSTANCE_URL="http://127.0.0.1:10020"
    DEFAULT_BACKEND_URL="ws://127.0.0.1:10020/api/execution-agents/connect"
    STATE_DIR="$HOME/state"
    STATE_FILE="$STATE_DIR/state"
    SERVICE_DIR="$HOME/systemd"
    SERVICE_FILE="$SERVICE_DIR/matoloop-agent.service"
    CHECKOUT_CANDIDATE="$HOME/checkout"
    [[ "$(detect_project_root "$CHECKOUT_CANDIDATE" "$HOME")" == "$DEFAULT_SOURCE_DIR" ]]
    mkdir -p "$CHECKOUT_CANDIDATE/apps/matoloop-agent"
    : > "$CHECKOUT_CANDIDATE/apps/matoloop-agent/pyproject.toml"
    [[ "$(detect_project_root "$CHECKOUT_CANDIDATE" "$HOME")" == "$CHECKOUT_CANDIDATE" ]]
    PROJECT_ROOT="$DEFAULT_SOURCE_DIR"
    mkdir -p "$PROJECT_ROOT/apps/matoloop-agent"
    reset_state

    prompt_value prompted "Test value" "default" <<< ""
    [[ "$prompted" == default ]]
    prompt_value prompted "Test value" "default" <<< "custom"
    [[ "$prompted" == custom ]]
    validate_instance_url "http://127.0.0.1:10020"
    validate_instance_url "https://mato.example/base/"
    ! validate_instance_url "ws://mato.example"
    ! validate_instance_url "HTTP://mato.example"
    ! validate_instance_url "https://mato.example?token=x"
    ! validate_instance_url "https://mato.example?"
    ! validate_instance_url "https://mato.example#"
    [[ "$(instance_to_backend_url 'http://127.0.0.1:10020/')" == "ws://127.0.0.1:10020/api/execution-agents/connect" ]]
    [[ "$(instance_to_backend_url 'https://mato.example/base')" == "wss://mato.example/base/api/execution-agents/connect" ]]
    [[ "$(backend_to_instance_url 'wss://mato.example/base/api/execution-agents/connect')" == "https://mato.example/base" ]]
    [[ "$(backend_to_instance_url 'wss://mato.example/socket/execution-agents/connect')" == "https://mato.example/socket" ]]
    safe_managed_path "$HOME/mato-loop-agent/data"
    ! safe_managed_path "$HOME"
    is_same_or_parent "$HOME/mato-loop-agent" "$HOME/mato-loop-agent/data"
    ! is_same_or_parent "$HOME/mato-loop-agent/data" "$HOME/mato-loop-agent"
    [[ "$(systemd_quote '/home/test agent/100%')" == '"/home/test agent/100%%"' ]]
    [[ "$(installation_state)" == absent ]]

    mkdir -p "$STATE_DIR"
    printf '%s\n' \
        "dataDir=$DEFAULT_DATA_DIR" \
        "workspaceDir=$DEFAULT_WORKSPACE_DIR" \
        "backendUrl=wss://mato.example/socket/execution-agents/connect" \
        "sourceRoot=$PROJECT_ROOT" \
        "venvCreated=false" \
        "workspaceCreated=false" \
        "lingerCreated=false" \
        "sourceCreated=false" > "$STATE_FILE"
    load_state
    [[ "$INSTANCE_URL" == "https://mato.example/socket" ]]
    [[ "$BACKEND_URL" == "wss://mato.example/socket/execution-agents/connect" ]]
    [[ "$BACKEND_DERIVED" == false ]]
    rm -f "$STATE_FILE"
    reset_state

    DATA_DIR="$DEFAULT_DATA_DIR"
    WORKSPACE_DIR="$DEFAULT_WORKSPACE_DIR"
    SOURCE_ROOT="$PROJECT_ROOT"
    save_state
    [[ "$(installation_state)" == partial ]]
    mkdir -p "$DATA_DIR" "$(dirname "$SERVICE_FILE")" "$PROJECT_ROOT/apps/matoloop-agent/.venv/bin"
    : > "$DATA_DIR/config.json"
    : > "$PROJECT_ROOT/apps/matoloop-agent/.venv/bin/matoloop-agent"
    chmod 700 "$PROJECT_ROOT/apps/matoloop-agent/.venv/bin/matoloop-agent"
    write_service
    [[ "$(< "$SERVICE_FILE")" != *"WorkingDirectory="* ]]
    [[ "$(installation_state)" == installed ]]

    rm -rf -- "$PROJECT_ROOT" "$STATE_DIR" "$SERVICE_DIR"
    fixture="$HOME/fixture"
    mkdir -p "$fixture/apps/matoloop-agent"
    : > "$fixture/apps/matoloop-agent/pyproject.toml"
    git init -q "$fixture"
    git -C "$fixture" add apps/matoloop-agent/pyproject.toml
    git -C "$fixture" -c user.name=MatoLoop -c user.email=installer@example.invalid commit -qm fixture
    PROJECT_ROOT="$DEFAULT_SOURCE_DIR"
    AGENT_PROJECT="$PROJECT_ROOT/apps/matoloop-agent"
    VENV="$AGENT_PROJECT/.venv"
    SOURCE_URL="$fixture"
    reset_state
    prepare_source
    [[ "$SOURCE_CREATED" == true ]]
    [[ -f "$PROJECT_ROOT/apps/matoloop-agent/pyproject.toml" ]]
    [[ -f "$PROJECT_ROOT/.git/matoloop-installer-managed" ]]
    printf 'Installer self-test passed.\n'
)

main() {
    local action="${1:-}"
    local option="${2:-}"
    if [[ -z "$action" ]]; then
        choose_action
        action="$SELECTED_ACTION"
    fi
    case "$action" in
        install|repair) [[ -z "$option" ]] || { usage >&2; exit 2; }; install_or_repair ;;
        status) [[ -z "$option" ]] || { usage >&2; exit 2; }; show_status ;;
        uninstall)
            case "$option" in
                '') uninstall_agent false ;;
                --yes) uninstall_agent true ;;
                *) usage >&2; exit 2 ;;
            esac
            ;;
        --self-test) [[ -z "$option" ]] || { usage >&2; exit 2; }; self_test ;;
        help|-h|--help) usage ;;
        exit) ;;
        *) usage >&2; exit 2 ;;
    esac
}

main "$@"
