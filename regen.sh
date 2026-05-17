#!/usr/bin/env bash
# Rebuild index.html, people.html, manifest.webmanifest from the
# people/*.md files and the cached program HTML — then encrypt the
# data blobs with the PIN so the public site stays gated.
#
# Usage:   PIN=0849 ./regen.sh
set -euo pipefail
cd "$(dirname "$0")"

if [[ -z "${PIN:-}" ]]; then
  echo "Set PIN=<4 digits> before running (e.g. PIN=0849 ./regen.sh)" >&2
  exit 1
fi

PY="/Users/brinkmann/repros/research/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi

"$PY" build.py "$@"
"$PY" encrypt.py
