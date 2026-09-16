#!/usr/bin/env bash
# Seed the demo dataset used by the UI capture pipeline (idempotent).
# Repo-owned helper — the family capture wrapper just runs seed.command.
# Reads HA_DEMO_EMAIL / HA_DEMO_PASSWORD from the environment (the capture
# wrapper already exported the root .env), mirrors run-dev.sh's venv detection.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$ROOT/backend"

PY_BIN=""
for candidate in "$BACKEND/venv/bin/python" "$BACKEND/.venv/bin/python"; do
  if [[ -x "$candidate" ]]; then PY_BIN="$candidate"; break; fi
done
if [[ -z "$PY_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then PY_BIN="python3"
  elif command -v python >/dev/null 2>&1; then PY_BIN="python"
  else
    echo "❌ No Python interpreter found. Run ./scripts/run-dev.sh first to create the backend venv."
    exit 1
  fi
fi

echo "→ Seeding demo data (using $PY_BIN)…"
( cd "$BACKEND" && PYTHONPATH="$(pwd):$(pwd)/.." "$PY_BIN" scripts/seed_demo.py )
