#!/usr/bin/env bash
# Thin shim — the capture pipeline lives in scripts/ui-capture/ (family-standard
# vendored scripts; ui-capture.config.json + scenes.mjs are repo-owned).
# All flags are forwarded verbatim.
#
# Usage:
#   ./scripts/capture_ui.sh                   # all scenes, both viewports
#   ./scripts/capture_ui.sh --scene dashboard
#   ./scripts/capture_ui.sh --viewport desktop
#   ./scripts/capture_ui.sh --gallery-only     # just rebuild docs/SCREENSHOTS.md
#   ./scripts/capture_ui.sh --strict           # fail fast on broken pages
#   ./scripts/capture_ui.sh -h | --help        # pipeline help
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/ui-capture/capture_ui.sh" "$@"
