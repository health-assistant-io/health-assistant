#!/usr/bin/env bash
# Family UI capture wrapper (template) — captures screenshots, rebuilds the
# Markdown gallery, compresses PNGs and assembles the animated tour GIF.
#
# All repo-specific facts (URLs, seed command, GIF order, output paths) are
# read from ui-capture.config.json — edit the config, not this script.
#
# Prerequisites (handled here where possible):
#   - app running                    (checked against app.healthUrls)
#   - demo data seeded               (runs seed.command; skipped for --gallery-only)
#   - playwright + chromium          (installed into project.frontendDir on demand)
#   - pngquant / ffmpeg / gifsicle   (optional; compression steps are skipped if missing)
#
# Usage:
#   ./scripts/ui-capture/capture_ui.sh                  # all scenes, both viewports
#   ./scripts/ui-capture/capture_ui.sh --scene dashboard
#   ./scripts/ui-capture/capture_ui.sh --viewport desktop
#   ./scripts/ui-capture/capture_ui.sh --gallery-only    # just rebuild the gallery
#   ./scripts/ui-capture/capture_ui.sh --strict          # fail fast on broken pages
#   ./scripts/ui-capture/capture_ui.sh -h | --help       # print this help and exit
#
# Other flags (e.g. --base, --api, --login) are forwarded verbatim to the
# capture runner (capture.mjs); see its --help for the full surface.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
CAPTURE="$DIR/capture.mjs"
NODE_BIN="${NODE_BIN:-node}"

print_help() { sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
for _arg in "$@"; do
  case "$_arg" in
    -h|--help) print_help ;;
  esac
done

# Single source of truth for all resolved values is capture.mjs (it loads
# ui-capture.config.json + the root .env). Each --print call returns one value.
print_val() { ( cd "$ROOT" && "$NODE_BIN" "$CAPTURE" --print "$1" ); }

# Export the root .env (ports, demo credentials) so the seed command and any
# manual override of the same vars agree with how the app stack was started.
# Tolerate absence so this still runs in fresh clones without an .env yet.
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

GALLERY_ONLY=false
for _arg in "$@"; do
  case "$_arg" in --gallery-only) GALLERY_ONLY=true ;; esac
done

FRONTEND_DIR="$(print_val frontendDir)"
OUT_DIR="$(print_val outDir)"

# ---- liveness checks -------------------------------------------------------
if [[ "$GALLERY_ONLY" == false ]]; then
  check_url() { curl -fsS -o /dev/null -m 3 "$1" 2>/dev/null; }
  while IFS= read -r url; do
    [[ -z "$url" ]] && continue
    if ! check_url "$url"; then
      echo "❌ Not reachable: $url"
      echo "   Start the app first, or fix app.healthUrls in ui-capture.config.json."
      exit 1
    fi
  done < <(print_val healthUrls)

  # ---- seed demo data (idempotent, config-defined) -------------------------
  SEED_CMD="$(print_val seed)"
  if [[ -n "$SEED_CMD" ]]; then
    echo "→ Seeding demo data…"
    ( cd "$ROOT" && bash -c "$SEED_CMD" )
  fi
fi

# ---- ensure Playwright + chromium ------------------------------------------
if ! ( cd "$FRONTEND_DIR" && "$NODE_BIN" -e "require.resolve('playwright')" 2>/dev/null ); then
  echo "→ Installing Playwright…"
  if [[ -f "$FRONTEND_DIR/pnpm-lock.yaml" ]]; then
    ( cd "$FRONTEND_DIR" && pnpm install --no-frozen-lockfile )
  else
    ( cd "$FRONTEND_DIR" && npm install --no-audit --no-fund )
  fi
fi
if ! ( cd "$FRONTEND_DIR" && npx playwright --version 2>/dev/null | grep -q . ); then
  echo "→ Installing chromium for Playwright…"
  ( cd "$FRONTEND_DIR" && npx playwright install chromium )
fi

# ---- capture ----------------------------------------------------------------
echo "→ Capturing scenes…"
( cd "$FRONTEND_DIR" && "$NODE_BIN" "$CAPTURE" "$@" )

# ---- compress PNGs -----------------------------------------------------------
if command -v pngquant >/dev/null 2>&1; then
  echo "→ Compressing PNGs with pngquant…"
  # --skip-if-larger prevents writing a file if compression increases size;
  # --speed 1 is slower but yields better quality/compression.
  find "$OUT_DIR" -name "*.png" -print0 | xargs -0 -I {} pngquant --ext .png --force --skip-if-larger --speed 1 {}
  echo "✅ PNGs compressed"
else
  echo "ℹ 'pngquant' not found. Skipping PNG compression."
  echo "  To enable: sudo apt-get install pngquant (Ubuntu/Debian) or brew install pngquant (macOS)"
fi

# ---- assemble the animated tour GIF ------------------------------------------
if command -v ffmpeg >/dev/null 2>&1; then
  GIF_OUT="$(print_val gifOut)"
  GIF_WIDTH="$(print_val gifWidth)"
  FRAME_SECONDS="$(print_val frameSeconds)"
  HOLD_LAST="$(print_val holdLast)"
  echo "→ Generating animated tour GIF…"
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TMP_DIR"' EXIT

  i=0
  last_img=""
  while IFS= read -r scene; do
    [[ -z "$scene" ]] && continue
    img="$OUT_DIR/${scene}-desktop.png"
    if [[ -f "$img" ]]; then
      cp "$img" "$TMP_DIR/$(printf "%02d" "$i").png"
      last_img="$img"
      i=$((i + 1))
    fi
  done < <(print_val gifOrder)

  # Hold the final frame a little longer so the loop doesn't feel abrupt.
  if [[ -n "$last_img" ]]; then
    for ((h = 0; h < HOLD_LAST; h++)); do
      cp "$last_img" "$TMP_DIR/$(printf "%02d" "$i").png"
      i=$((i + 1))
    done
  fi

  if [[ "$i" -eq 0 ]]; then
    echo "ℹ No desktop PNGs found for the GIF — skipping (run without --gallery-only first)."
  else
    # 1/N fps = FRAME_SECONDS seconds per frame; palettegen/paletteuse keeps
    # the GIF small and colors accurate.
    ffmpeg -y -framerate "1/$FRAME_SECONDS" -pattern_type glob -i "$TMP_DIR/*.png" \
      -vf "scale=${GIF_WIDTH}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
      -loop 0 "$GIF_OUT" -hide_banner -loglevel error
    echo "✅ GIF generated at ${GIF_OUT#"$ROOT"/}"

    if command -v gifsicle >/dev/null 2>&1; then
      echo "→ Compressing GIF with gifsicle…"
      # -O3 max optimization; --lossy=80 trades a little quality for a big size drop.
      gifsicle -O3 --lossy=80 -o "$GIF_OUT" "$GIF_OUT"
      echo "✅ GIF compressed"
    else
      echo "ℹ 'gifsicle' not found. Skipping GIF compression."
      echo "  To enable: sudo apt-get install gifsicle (Ubuntu/Debian) or brew install gifsicle (macOS)"
    fi
  fi
else
  echo "ℹ 'ffmpeg' not found. Skipping animated GIF generation."
  echo "  To enable: sudo apt-get install ffmpeg (Ubuntu/Debian) or brew install ffmpeg (macOS)"
fi

echo "✅ Done — see $(print_val gallery | sed "s|$ROOT/||")"
