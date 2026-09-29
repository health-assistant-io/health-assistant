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

# Explicit demo-database override (family demo-tour standard): points the
# seeder at a *_demo database outside the backend/.env default — used when
# the capture targets a dedicated demo instance (e.g. a neuro_health_demo
# database on the dev PostgreSQL container). The §13 guards still apply.
if [[ -n "${HA_DEMO_DATABASE_URL:-}" ]]; then
  INIT_FLAG=""
  if [[ "${HA_DEMO_INIT:-}" == "true" || "${HA_DEMO_INIT:-}" == "1" ]]; then INIT_FLAG="--init-demo"; fi
  ( cd "$BACKEND" && PYTHONPATH="$(pwd):$(pwd)/.." "$PY_BIN" scripts/seed_demo.py --database-url "$HA_DEMO_DATABASE_URL" $INIT_FLAG )
  exit 0
fi

( cd "$BACKEND" && PYTHONPATH="$(pwd):$(pwd)/.." "$PY_BIN" scripts/seed_demo.py )
