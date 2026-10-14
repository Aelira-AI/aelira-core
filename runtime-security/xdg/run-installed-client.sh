#!/usr/bin/env bash
set -euo pipefail
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp > /work/xvfb.log 2>&1 &
server=$!
trap 'kill "$server" 2>/dev/null || true' EXIT
export DISPLAY=:99
for unused in {1..100}; do
  kill -0 "$server"
  test ! -S /tmp/.X11-unix/X99 || break
  sleep 0.1
done
test -S /tmp/.X11-unix/X99
dbus-run-session -- python3 -B /recipe/test-installed-client.py
