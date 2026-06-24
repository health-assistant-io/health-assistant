#!/usr/bin/env bash
# Capture UI screenshots for the docs and regenerate the Markdown gallery.
#
# Prerequisites (handled here where possible):
#   - backend + frontend running            (./scripts/run-dev.sh)
#   - demo tenant/user/patients seeded      (backend/scripts/seed_demo.py)
#   - playwright + chromium installed       (npm install && npx playwright install chromium)
#
# Ports, URLs and demo credentials are read from the root .env (the same file
# run-dev.sh sources), so this script stays in sync with how the stack was
# started. Override per-run by exporting the same vars or passing --base/--api.
#
# Usage:
#   ./scripts/capture_ui.sh                 # all scenes, both viewports
#   ./scripts/capture_ui.sh --scene dashboard
#   ./scripts/capture_ui.sh --viewport desktop
#   ./scripts/capture_ui.sh --gallery-only   # just rebuild docs/SCREENSHOTS.md
#   ./scripts/capture_ui.sh --strict         # fail fast on broken pages
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="$ROOT/frontend"
BACKEND="$ROOT/backend"

# Load the root .env (ports, demo creds) — same used by run-dev.sh. Tolerate
# absence so this still runs in fresh clones without an .env yet.
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

# Resolve URLs from env, falling back to localhost:<PORT> from .env, then
# hardcoded defaults. HA_FRONTEND_URL / HA_API_URL win outright when set.
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_URL="${HA_FRONTEND_URL:-http://localhost:${FRONTEND_PORT}}"
API_URL="${HA_API_URL:-http://localhost:${BACKEND_PORT}/api/v1}"

# Liveness probe target for the backend. The API base (/api/v1) has no index
# route and returns 404 on GET /, which would false-fail a `curl -f` check.
# Probe the root /health endpoint instead (registered outside the v1 router).
# Derive the backend root from API_URL by stripping a trailing /api/v1.
BACKEND_ROOT_URL="${API_URL%/api/v1}"
BACKEND_ROOT_URL="${BACKEND_ROOT_URL%/}"
BACKEND_HEALTH_URL="${BACKEND_ROOT_URL}/health"

# Quick liveness checks so failures are obvious instead of cryptic.
check_url() {
  curl -fsS -o /dev/null -m 3 "$1" 2>/dev/null
}

if ! check_url "$FRONTEND_URL"; then
  echo "❌ Frontend not reachable at $FRONTEND_URL"
  echo "   (env: HA_FRONTEND_URL=${HA_FRONTEND_URL:-<unset>}, FRONTEND_PORT=$FRONTEND_PORT)"
  echo "   Start it first:  ./scripts/run-dev.sh"
  exit 1
fi
if ! check_url "$BACKEND_HEALTH_URL"; then
  echo "❌ Backend not reachable at $BACKEND_HEALTH_URL"
  echo "   (env: HA_API_URL=${HA_API_URL:-<unset>}, BACKEND_PORT=$BACKEND_PORT)"
  echo "   Start it first:  ./scripts/run-dev.sh"
  exit 1
fi

# Seed demo data (idempotent). Only needed when capturing, not for --gallery-only.
# Uses the backend virtualenv if present (mirrors run-dev.sh's venv detection),
# falling back to python3 / python so the seeder can run on a system install.
# The seeder reads HA_DEMO_EMAIL / HA_DEMO_PASSWORD from env (already exported).
if [[ "${1:-}" != "--gallery-only" ]]; then
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
fi

# Ensure Playwright is available.
if ! ( cd "$FRONTEND" && node -e "require.resolve('playwright')" 2>/dev/null ); then
  echo "→ Installing Playwright…"
  ( cd "$FRONTEND" && npm install --no-audit --no-fund )
fi
if ! ( cd "$FRONTEND" && npx playwright --version 2>/dev/null | grep -q . ); then
  echo "→ Installing chromium for Playwright…"
  ( cd "$FRONTEND" && npx playwright install chromium )
fi

# Forward resolved URLs to the runner as defaults; flags in $@ can still override.
echo "→ Capturing scenes…"
( cd "$FRONTEND" && node tests-e2e/ui-capture/capture.mjs --base "$FRONTEND_URL" --api "$API_URL" "$@" )


# Generate GIF if ffmpeg is available
if command -v ffmpeg >/dev/null 2>&1; then
    echo "→ Generating animated GIF tour..."
    
    GIF_OUT="$ROOT/docs/images/visual-tour.gif"
    TMP_DIR=$(mktemp -d)
    
    # Define explicitly ordered sequence of scenes for the GIF
    SCENES=("login" "dashboard" "patients" "patient-detail" "examinations" "examination-detail" "documents" "biomarkers" "biomarker-detail" "ai-chat")
    
    i=0
    for SCENE in "${SCENES[@]}"; do
        IMG="$ROOT/docs/images/${SCENE}-desktop.png"
        if [[ -f "$IMG" ]]; then
            # pad index to keep them sorted alphabetically (00.png, 01.png...)
            # use a long hold on the final frame (ai-chat) by duplicating it
            cp "$IMG" "$TMP_DIR/$(printf "%02d" $i).png"
            if [[ "$SCENE" == "ai-chat" ]]; then
                cp "$IMG" "$TMP_DIR/$(printf "%02d" $((i+1))).png"
                cp "$IMG" "$TMP_DIR/$(printf "%02d" $((i+2))).png"
                cp "$IMG" "$TMP_DIR/$(printf "%02d" $((i+3))).png"
            fi
            i=$((i+1))
        fi
    done
    
    # 0.5 fps = 2 seconds per image. Scale to standard width to keep size reasonable (800px)
    ffmpeg -y -framerate 1/2 -pattern_type glob -i "$TMP_DIR/*.png" -vf "scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" -loop 0 "$GIF_OUT" -hide_banner -loglevel error
    
    rm -rf "$TMP_DIR"
    echo "✅ GIF generated at docs/images/visual-tour.gif"
fi

echo "✅ Done — see docs/SCREENSHOTS.md"
