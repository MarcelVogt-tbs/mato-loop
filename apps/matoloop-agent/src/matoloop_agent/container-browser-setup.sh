#!/bin/sh
# Shared by image builds and managed container updates. Pass the target Python.
set -eu
PYTHON="$1"
export PLAYWRIGHT_BROWSERS_PATH=/usr/local/lib/matoloop-agent/tools/browser
# Keep the previous release's browser available until activation succeeds.
export PLAYWRIGHT_SKIP_BROWSER_GC=1
apt-get update
apt-get install --no-install-recommends -y xauth xvfb x11-xserver-utils x11-utils openbox x11vnc xclip
if ! id matoloop-browser >/dev/null 2>&1; then
    useradd --system --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin matoloop-browser
fi
"$PYTHON" -m playwright install --with-deps --no-shell chromium
chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH"
rm -rf /var/lib/apt/lists/*
