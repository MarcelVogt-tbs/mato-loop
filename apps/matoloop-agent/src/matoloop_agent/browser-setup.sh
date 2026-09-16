#!/bin/bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
umask 077

if [ "${MATOLOOP_AGENT_CONTAINER:-0}" = 1 ]; then
    printf '%s\n' 'Die Browser-Komponenten sind im Container enthalten. Die Startpruefung folgt.'
    command -v Xvfb xrandr xprop openbox x11vnc xclip >/dev/null
elif [ "$(uname -s)" != Linux ]; then
    printf '%s\n' 'Die Browser-Einrichtung wird auf diesem System noch nicht unterstuetzt.'
    exit 1
else
    printf '%s\n' 'Die passende Chromium-Version wird im Agent-Verzeichnis installiert und ohne grafische Oberfläche geprüft.'
fi
printf '%s\n' 'Die Browser-Funktionspruefung wird ausgefuehrt.'
exec "${MATOLOOP_BROWSER_EXECUTABLE}" -m matoloop_agent browser-install
