#!/usr/bin/env bash
# Rebuild index.html, people.html, manifest.webmanifest from the
# people/*.md files and the cached program HTML.
set -euo pipefail
cd "$(dirname "$0")"
PY="/Users/brinkmann/repros/research/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi
exec "$PY" build.py "$@"
