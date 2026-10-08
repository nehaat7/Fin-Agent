#!/usr/bin/env bash
# Builds Lambda bundles with Linux wheels (no Docker needed).
set -euo pipefail
cd "$(dirname "$0")"
PIP=".venv/bin/pip"
for fn in mcp_server agent; do
  out=".build/$fn"
  rm -rf "$out" && mkdir -p "$out"
  $PIP install -q -r "$fn/requirements.txt" -t "$out" \
    --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 \
    --implementation cp --python-version 3.13 --only-binary=:all: --upgrade
  cp "$fn"/*.py "$out"/
  [ -f "$fn/index.html" ] && cp "$fn/index.html" "$out"/
  find "$out" -name "__pycache__" -type d -prune -exec rm -rf {} +
  echo "built $out ($(du -sh "$out" | cut -f1))"
done
