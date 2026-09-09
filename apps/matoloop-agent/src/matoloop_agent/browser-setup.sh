#!/bin/bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
umask 077

if [ "${MATOLOOP_AGENT_CONTAINER:-0}" = 1 ]; then
    printf '%s\n' 'Die Browser-Komponenten sind im Container enthalten. Die Startpruefung folgt.'
    command -v Xvfb xrandr xprop openbox x11vnc xclip >/dev/null
elif [ "$(uname -s)" != Linux ] || ! command -v apt-get >/dev/null; then
    printf '%s\n' 'Die automatische Einrichtung benoetigt Debian oder Ubuntu.'
    exit 1
else
printf '%s\n' 'Paketinformationen werden aktualisiert. sudo kann nach Ihrem Passwort fragen.'
if [ "$(id -u)" = 0 ]; then
    apt-get update
else
    sudo -- apt-get update
fi
packages=(xvfb xauth x11-xserver-utils x11-utils openbox x11vnc xclip fonts-liberation fonts-noto-color-emoji
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3
    libatspi2.0-0 libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3
    libxrandr2 libgbm1 libxcb1 libxkbcommon0 libpango-1.0-0 libcairo2)
# Ubuntu 24.04 renamed this library; apt cannot resolve the old virtual package unambiguously.
if apt-cache show libasound2t64 >/dev/null 2>&1; then
    packages+=(libasound2t64)
else
    packages+=(libasound2)
fi
printf '%s\n' 'Systempakete werden installiert. sudo kann nach Ihrem Passwort fragen.'
if [ "$(id -u)" = 0 ]; then
    apt-get install --no-install-recommends -y "${packages[@]}"
    if ! id matoloop-browser >/dev/null 2>&1; then
        useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin matoloop-browser
    fi
else
    sudo -- apt-get install --no-install-recommends -y "${packages[@]}"
fi
printf '%s\n' 'Die passende Chromium-Version wird als Browser-Benutzer installiert.'
fi
printf '%s\n' 'Die grafische Browser-Funktionspruefung wird ausgefuehrt.'
if [ "${MATOLOOP_BROWSER_FROZEN}" = 1 ]; then
    exec "${MATOLOOP_BROWSER_EXECUTABLE}" browser-install
else
    exec "${MATOLOOP_BROWSER_EXECUTABLE}" -m matoloop_agent browser-install
fi
