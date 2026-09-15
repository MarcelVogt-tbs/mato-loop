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
    missing=()
    for command in Xvfb xrandr xprop openbox x11vnc xclip; do
        command -v "$command" >/dev/null || missing+=("$command")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        printf 'Fehlende Browser-Voraussetzungen: %s\n' "${missing[*]}"
        printf '%s\n' 'MatoLoop veraendert keine Systempakete. Installieren Sie die Voraussetzungen ueber Ihr Betriebssystem.'
        exit 1
    fi
    printf '%s\n' 'Die passende Chromium-Version wird im Agent-Verzeichnis installiert.'
fi
printf '%s\n' 'Die grafische Browser-Funktionspruefung wird ausgefuehrt.'
exec "${MATOLOOP_BROWSER_EXECUTABLE}" -m matoloop_agent browser-install
