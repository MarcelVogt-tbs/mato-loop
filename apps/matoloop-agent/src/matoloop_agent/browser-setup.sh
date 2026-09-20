#!/bin/bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
umask 077

check_tool() {
    if path=$(command -v "$1" 2>/dev/null); then
        printf '  [ok] %s: %s\n' "$1" "$path"
    else
        printf '  [missing] %s\n' "$1"
    fi
}

if [ "${MATOLOOP_AGENT_CONTAINER:-0}" = 1 ]; then
    printf '%s\n' 'Browser components are included in the container.'
    printf '%s\n' 'Checking browser desktop tools.'
    for tool in Xvfb xrandr xprop openbox x11vnc xclip; do
        check_tool "$tool"
    done
    command -v Xvfb xrandr xprop openbox x11vnc xclip >/dev/null
elif [ "$(uname -s)" != Linux ]; then
    printf '%s\n' 'Browser setup is not supported on this system yet.'
    exit 1
else
    printf '%s\n' 'Installing browser Python packages in the Agent environment.'
    "${MATOLOOP_BROWSER_UV_EXECUTABLE}" pip install \
        --python "${MATOLOOP_BROWSER_EXECUTABLE}" \
        'pillow>=12.1.1,<13' \
        'playwright==1.59.0'
    printf '%s\n' 'The matching Chromium version will be installed in the Agent directory.'
    printf '%s\n' 'Checking browser setup tools.'
    for tool in bash apt-get dpkg-query; do
        check_tool "$tool"
    done
    printf '%s\n' 'Checking browser system dependencies.'
    if "${MATOLOOP_BROWSER_EXECUTABLE}" -m matoloop_agent browser-install; then
        exit 0
    fi
    if command -v apt-get >/dev/null 2>&1 && command -v dpkg-query >/dev/null 2>&1 && [ -t 0 ]; then
        printf '%s' 'Install missing browser system dependencies? [Y/n] '
        read -r answer
        case "$answer" in
            n|N|no|NO|No) ;;
            *)
                before=$(mktemp)
                after=$(mktemp)
                trap 'rm -f "$before" "$after"' EXIT
                dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package}\n' |
                    sed -n 's/^ii  //p' | sort -u > "$before"
                "${MATOLOOP_BROWSER_EXECUTABLE}" -m playwright install-deps chromium
                dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package}\n' |
                    sed -n 's/^ii  //p' | sort -u > "$after"
                packages="${MATOLOOP_BROWSER_DATA_DIR}/../system-packages.txt"
                [ ! -L "$packages" ] || { printf '%s\n' 'Invalid system package tracking file.' >&2; exit 1; }
                { [ ! -f "$packages" ] || command cat "$packages"; comm -13 "$before" "$after"; } |
                    sort -u > "${packages}.tmp"
                chmod 600 "${packages}.tmp"
                mv "${packages}.tmp" "$packages"
                ;;
        esac
    else
        printf '%s\n' 'Automatic browser dependency installation requires an interactive Debian or Ubuntu terminal.'
    fi
fi
printf '%s\n' 'Running the browser check.'
exec "${MATOLOOP_BROWSER_EXECUTABLE}" -m matoloop_agent browser-install
