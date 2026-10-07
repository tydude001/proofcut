#!/bin/bash
# Start the headless browser cdp.mjs drives, on a port nobody else holds.
#
#   bash .claude/skills/verify-live/launch.sh [WIDTH,HEIGHT]
#
# Prints CDP_PORT=<n> — prefix every cdp.mjs call with it. Chrome picks the
# port itself (--remote-debugging-port=0) and writes it to DevToolsActivePort
# in its own profile dir. A fixed port is how a browser another session left
# running gets driven instead of yours: on 2026-10-05 the old one held
# 127.0.0.1:9333, the new one bound only [::1]:9333 without a word, and every
# call went to the old page (wiki tooling.md § Headless browser).
set -euo pipefail
size=${1:-1400,900}
bin=$(ls ~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | sort | tail -1)
[ -n "$bin" ] || { echo "no chrome-headless-shell — npx playwright install chromium-headless-shell" >&2; exit 1; }
profile=$(mktemp -d -p ~/.cache verify-live.XXXXXX)
setsid nohup "$bin" --remote-debugging-port=0 --user-data-dir="$profile" \
  --headless --disable-gpu --no-sandbox --window-size="$size" about:blank \
  >"$profile/chrome.log" 2>&1 </dev/null &
pid=$!
for _ in $(seq 100); do
  [ -s "$profile/DevToolsActivePort" ] && break
  kill -0 "$pid" 2>/dev/null || { echo "browser exited — $profile/chrome.log:" >&2; tail -5 "$profile/chrome.log" >&2; exit 1; }
  sleep 0.1
done
[ -s "$profile/DevToolsActivePort" ] || { echo "no DevToolsActivePort after 10s" >&2; kill "$pid"; exit 1; }
echo "CDP_PORT=$(head -1 "$profile/DevToolsActivePort")"
echo "stop it with: kill $pid && rm -rf $profile"
