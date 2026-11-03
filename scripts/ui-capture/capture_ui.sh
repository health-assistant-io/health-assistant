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
# Parsed line-by-line (NOT sourced): unquoted values with spaces
# (APP_NAME=Career Assistant) or stray shell syntax would otherwise be
# executed. Matches capture.mjs's dotenv semantics: quotes stripped,
# trailing " # …" comments dropped, KEY must look like an identifier.
if [[ -f "$ROOT/.env" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    val="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    val="${val#"${val%%[![:space:]]*}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ "$val" == \"*\" && "$val" == *\" && ${#val} -ge 2 ]]; then
      val="${val:1:${#val}-2}"
    elif [[ "$val" == \'*\' && "$val" == *\' && ${#val} -ge 2 ]]; then
      val="${val:1:${#val}-2}"
    else
      val="${val%% #*}"
      val="${val%"${val##*[![:space:]]}"}"
    fi
    export "$key=$val"
  done < "$ROOT/.env"
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
# Playwright the package is needed in every mode; its own chromium browser is
# needed only in url mode — cdp mode captures the app's own browser windows.
# NOTE: `npm/pnpm install` only installs what package.json already lists —
# playwright must be a devDependency of project.frontendDir (adoption step).
# The --no-save fallback below covers a maintainer machine that hasn't added
# it yet, without dirtying the lockfile.
ensure_playwright_pkg() {
  ( cd "$FRONTEND_DIR" && "$NODE_BIN" -e "try{require.resolve('playwright');process.exit(0)}catch{};try{require.resolve('@playwright/test');process.exit(0)}catch{};process.exit(1)" 2>/dev/null )
}
if ! ensure_playwright_pkg; then
  echo "→ Installing frontend dependencies…"
  # pnpm workspaces keep pnpm-lock.yaml at the repo root, not in frontendDir.
  if [[ -f "$FRONTEND_DIR/pnpm-lock.yaml" || -f "$ROOT/pnpm-lock.yaml" ]]; then
    ( cd "$FRONTEND_DIR" && pnpm install --no-frozen-lockfile )
  else
    ( cd "$FRONTEND_DIR" && npm install --no-audit --no-fund )
  fi
fi
if ! ensure_playwright_pkg && [[ ! -f "$FRONTEND_DIR/pnpm-lock.yaml" && ! -f "$ROOT/pnpm-lock.yaml" ]]; then
  echo "→ Playwright still missing — installing (no-save)…"
  ( cd "$FRONTEND_DIR" && npm install --no-save --no-audit --no-fund playwright )
fi
if ! ensure_playwright_pkg; then
  echo "❌ Playwright not found. Add it as a devDependency of $FRONTEND_DIR:"
  echo "   npm install -D playwright   (or: pnpm add -D playwright)"
  exit 1
fi
if [[ "$(print_val mode)" != "cdp" ]] && ! ( cd "$FRONTEND_DIR" && npx playwright --version 2>/dev/null | grep -q . ); then
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
  # --speed 1 is slower but yields better quality/compression. Exit 98 means
  # "kept the original (already smaller)" — expected on re-runs, not an error.
  # (Plain xargs + set -e would abort the whole pipeline on 98.)
  find "$OUT_DIR" -name "*.png" -print0 | while IFS= read -r -d '' f; do
    pngquant --ext .png --force --skip-if-larger --speed 1 "$f" || {
      rc=$?
      [[ $rc -eq 98 ]] || echo "  ⚠ pngquant $f (exit $rc)"
    }
  done
  echo "✅ PNGs compressed"
else
  echo "ℹ 'pngquant' not found. Skipping PNG compression."
  echo "  To enable: sudo apt-get install pngquant (Ubuntu/Debian) or brew install pngquant (macOS)"
fi

# ---- assemble the animated tour GIF ------------------------------------------
if command -v ffmpeg >/dev/null 2>&1; then
  GIF_OUT="$(print_val gifOut)"
  GIF_WIDTH="$(print_val gifWidth)"
  GIF_STAGE_HEIGHT="$(print_val gifStageHeight)"
  FRAME_SECONDS="$(print_val frameSeconds)"
  HOLD_LAST="$(print_val holdLast)"
  TRANSPARENT_BG="$(print_val transparentBackground)"
  echo "→ Generating animated tour GIF…"
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TMP_DIR"' EXIT

  # Frames come from the manifest in story order with per-frame hold times
  # (`file<TAB>holdMs`). Manifests without frames (older captures) fall back
  # to the scene stills at the configured seconds-per-frame.
  FRAME_ROWS="$(print_val gifFrames)"
  if [[ -z "$FRAME_ROWS" ]]; then
    FRAME_ROWS="$(
      while IFS=$'\t' read -r _scene file; do
        [[ -n "$file" ]] && printf '%s\t%s\n' "$file" "$((FRAME_SECONDS * 1000))"
      done < <(print_val gifFiles)
    )"
  fi

  frame_files=()
  frame_holds=()
  while IFS=$'\t' read -r file hold; do
    [[ -z "$file" ]] && continue
    if [[ ! -f "$OUT_DIR/$file" ]]; then
      echo "  ⚠ gif frame missing: $file"
      continue
    fi
    [[ "$hold" =~ ^[0-9]+$ ]] || hold=$((FRAME_SECONDS * 1000))
    frame_files+=("$file")
    frame_holds+=("$hold")
  done <<< "$FRAME_ROWS"

  if [[ "${#frame_files[@]}" -eq 0 ]]; then
    echo "ℹ No scene PNGs found for the GIF — skipping (run without --gallery-only first)."
  else
    # GIF frames must share one canvas, but full-page screenshots differ in
    # height (content-driven). Scale to width and pad every frame to the
    # tallest scaled height. The mismatched-size chain otherwise silently
    # collapses the palette filter to one frame.
    MAX_H=0
    for file in "${frame_files[@]}"; do
      dims="$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$OUT_DIR/$file")"
      fw="${dims%,*}"; fh="${dims#*,}"
      [[ -n "$fw" && -n "$fh" && "$fw" -gt 0 ]] || continue
      scaled=$(( (fh * GIF_WIDTH + fw / 2) / fw ))
      (( scaled > MAX_H )) && MAX_H=$scaled
    done
    [[ "$MAX_H" -eq 0 ]] && MAX_H=$GIF_WIDTH
    # A configured stage height gives every GIF a predictable 16:9-ish
    # frame instead of the tallest screenshot's height: tall app windows
    # scale down inside the stage, short launcher bars stay centred with
    # transparent padding. 0 = legacy content-height canvas.
    if [[ "$GIF_STAGE_HEIGHT" =~ ^[0-9]+$ ]] && (( GIF_STAGE_HEIGHT > 0 )); then
      MAX_H=$GIF_STAGE_HEIGHT
    fi

    # Canvas fill: opaque captures get a DARK fill — a white canvas makes the
    # compact launcher/toolbar frames read as blank. Transparent captures
    # (`capture.transparentBackground`) keep their alpha, so the canvas stays
    # transparent (one GIF composites onto light and dark surfaces) with a
    # palette that reserves a transparent entry.
    PAD_FILTER="pad=${GIF_WIDTH}:${MAX_H}:(ow-iw)/2:(oh-ih)/2:color=0x111827"
    PALETTE_FILTER="split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
    if [[ "$TRANSPARENT_BG" == "1" ]]; then
      PAD_FILTER="pad=${GIF_WIDTH}:${MAX_H}:(ow-iw)/2:(oh-ih)/2:color=black@0,format=rgba"
      PALETTE_FILTER="split[s0][s1];[s0]palettegen=reserve_transparent=1[p];[s1][p]paletteuse=alpha_threshold=128"
    fi

    # Normalize every frame onto the shared canvas FIRST (one ffmpeg call
    # per file) — a single-pass chain over a stream of mixed-size frames
    # collapses to a fraction of the frames. Uniform inputs then assemble
    # through the concat demuxer with explicit durations.
    mkdir -p "$TMP_DIR/n"
    for idx in "${!frame_files[@]}"; do
      ffmpeg -nostdin -y -v error -i "$OUT_DIR/${frame_files[$idx]}" -vf "scale=${GIF_WIDTH}:${MAX_H}:force_original_aspect_ratio=decrease:flags=lanczos,${PAD_FILTER}" \
        "$TMP_DIR/n/$(printf "%02d" "$idx").png"
    done

    # Per-frame delays: concat uses each entry's duration until the next
    # `file`. The final entry's duration is ignored, so the last frame is
    # repeated HOLD_LAST + 1 times (the loop's final flash is one tick).
    : > "$TMP_DIR/concat.txt"
    last_idx=$(( ${#frame_files[@]} - 1 ))
    for idx in "${!frame_files[@]}"; do
      hold_ms="${frame_holds[$idx]}"
      printf "file '%s'\nduration %d.%03d\n" \
        "$TMP_DIR/n/$(printf "%02d" "$idx").png" "$((hold_ms / 1000))" "$((hold_ms % 1000))" >> "$TMP_DIR/concat.txt"
    done
    last_hold="${frame_holds[$last_idx]}"
    for ((h = 0; h <= HOLD_LAST; h++)); do
      printf "file '%s'\nduration %d.%03d\n" \
        "$TMP_DIR/n/$(printf "%02d" "$last_idx").png" "$((last_hold / 1000))" "$((last_hold % 1000))" >> "$TMP_DIR/concat.txt"
    done

    ffmpeg -nostdin -y -f concat -safe 0 -i "$TMP_DIR/concat.txt" \
      -vf "$PALETTE_FILTER" \
      -loop 0 "$GIF_OUT" -hide_banner -loglevel error
    echo "✅ GIF generated at ${GIF_OUT#"$ROOT"/} (${#frame_files[@]} frames, ${GIF_WIDTH}x${MAX_H})"

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
