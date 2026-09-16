#!/bin/bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
umask 077

if [ "${MATOLOOP_AGENT_CONTAINER:-0}" = 1 ]; then
    printf '%s\n' 'Browser components are included in the container.'
    command -v Xvfb xrandr xprop openbox x11vnc xclip >/dev/null
elif [ "$(uname -s)" != Linux ]; then
    printf '%s\n' 'Browser setup is not supported on this system yet.'
    exit 1
else
    printf '%s\n' 'The matching Chromium version will be installed in the Agent directory.'
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
