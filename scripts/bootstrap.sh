#!/usr/bin/env bash
#
# Health Assistant — remote bootstrap (one-line install), generated.
#
# Generated from the family installer contract. Do not edit a rendered
# copy in place; change the contract/template and regenerate. The
# rendered script is published as a release asset: read it before running.
#
# What this script does, in the open: resolves a stable release,
# downloads the source-free runtime bundle with its manifest and
# SHA256SUMS from the GitHub release, verifies the bundle's SHA-256
# BEFORE extracting anything, extracts it to the install directory and
# hands off to the bundle's own scripts/install.sh with your terminal
# attached — so its interactive questions still reach you even though
# this script's stdin may be the curl pipe.
#
# It never builds images, never generates secrets, never starts Compose,
# installs no system packages and refuses to run as root. Re-running
# against an existing install re-runs THAT install's own installer
# (nothing is downloaded, your .env is never overwritten).
#
# Usage:
#   curl -fsSL https://neuronection.com/install/health.sh | bash
#   curl -fsSL https://neuronection.com/install/health.sh | bash -s -- --dry-run
#   curl -fsSL https://neuronection.com/install/health.sh | bash -s -- --dir /srv/health
#   curl -fsSL https://neuronection.com/install/health.sh | bash -s -- --dir /srv/health -- --http-port 8090
#   bash this-file.sh --help
#
# Flags:
#   --dir DIR         install directory (default: ~/health-assistant)
#   --ref REF         release tag (default: latest stable release; never a branch)
#   --dry-run         resolve + fetch + verify, print the hand-off, write
#                     nothing outside a temp dir
#   --version         print the bootstrap contract version + resolved release
#   --offline BUNDLE  install from a local bundle (SHA256SUMS beside it)
#   --image REF       override the image pin (advanced; KEY=REF names one image)
#   --edge            reserved: the explicit branch path — accepted and
#                     refused, loudly (a branch is not a release)
#   --allow-root      escape hatch for CI/disposable containers only
#   --                forward everything after this to scripts/install.sh
#   -h, --help        print this help and exit
#
# Environment overrides:
#   HEALTH_INSTALL_DIR       same as --dir
#   HEALTH_REF               same as --ref
#   HEALTH_BUNDLE            same as --offline
#   HEALTH_EXPECTED_SHA256   pin a known-good bundle SHA-256
#   HEALTH_IMAGE             same as --image
#
# Requires: bash >= 4, curl, tar, sha256sum (or 'shasum -a 256'), Docker.
# This script deliberately installs nothing for you; when a tool is
# missing it prints the official install page and exits.
#
# Exit codes: 0 ok · 1 usage/prerequisite/root refusal · 2 download/verify.
# Docs: https://health-assistant.io/docs

set -euo pipefail

# ── generated product config (do not edit; re-render from dev) ──────────────
readonly PRODUCT="health"
readonly TITLE="Health Assistant"
readonly REPO="health-assistant-io/health-assistant"
readonly BUNDLE_PREFIX="health-compose"
readonly ENV_PREFIX="HEALTH"
readonly CONTRACT_VERSION="1"
# shellcheck disable=SC2088
# rendered as ~/<dir> on purpose; the code expands it explicitly via ${DIR/#\~/$HOME}
readonly DEFAULT_DIR="~/health-assistant"
readonly DOCS_URL="https://health-assistant.io/docs"
readonly MIN_RAM_GB="8"
readonly IMAGE_KEYS="backend frontend"

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  printf 'error: bash >= 4 is required (this is bash %s).\n' "$BASH_VERSION" >&2
  exit 1
fi

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
else
  GREEN=''; YELLOW=''; NC=''
fi

info() { printf "${GREEN}==>${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}warning:${NC} %s\n" "$*"; }
die()  { # die CODE MESSAGE
  printf 'error: %s\n' "$2" >&2
  exit "$1"
}
usage_error() { # usage_error MESSAGE
  printf 'error: %s\n' "$1" >&2
  printf 'Run with --help for usage.\n' >&2
  exit 1
}

print_help() {
  cat <<EOF
$TITLE bootstrap — installer contract $CONTRACT_VERSION

Resolves a stable release, downloads the source-free runtime bundle,
verifies its SHA-256 against SHA256SUMS, extracts it, and hands off to
the bundle's own scripts/install.sh. Never builds, never generates
secrets, never starts Compose, installs no packages, refuses to run as
root, and re-runs an existing install's own installer instead of
overwriting anything.

Usage:
  curl -fsSL https://neuronection.com/install/$PRODUCT.sh | bash
  curl -fsSL https://neuronection.com/install/$PRODUCT.sh | bash -s -- --dry-run
  curl -fsSL https://neuronection.com/install/$PRODUCT.sh | bash -s -- --dir /srv/$PRODUCT -- --http-port 8090

Flags:
  --dir DIR         install directory (default: $DEFAULT_DIR)
  --ref REF         release tag (default: latest stable release; never a branch)
  --dry-run         resolve + fetch + verify, print the hand-off, write nothing
  --version         print the bootstrap contract version + resolved release
  --offline BUNDLE  install from a local bundle (SHA256SUMS beside it)
  --image REF       override the image pin (advanced; KEY=REF names one image)
  --edge            reserved: the explicit branch path — refused, loudly
  --allow-root      escape hatch for CI/disposable containers only
  --                forward everything after this to scripts/install.sh
  -h, --help        print this help and exit

Environment overrides:
  ${ENV_PREFIX}_INSTALL_DIR       same as --dir
  ${ENV_PREFIX}_REF               same as --ref
  ${ENV_PREFIX}_BUNDLE            same as --offline
  ${ENV_PREFIX}_EXPECTED_SHA256   pin a known-good bundle SHA-256
  ${ENV_PREFIX}_IMAGE             same as --image

Exit codes: 0 ok · 1 usage/prerequisite/root refusal · 2 download/verify
Docs: $DOCS_URL
EOF
  exit 0
}

# env_opt SUFFIX -> value of ${ENV_PREFIX}_SUFFIX, or empty when unset
env_opt() {
  local name="${ENV_PREFIX}_${1}"
  printf '%s' "${!name:-}"
}

need_cmd() { command -v "$1" >/dev/null 2>&1; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# host_of URL -> hostname (scheme, path, query and port stripped)
host_of() {
  local h="$1"
  h="${h#*://}"
  h="${h%%/*}"
  h="${h%%\?*}"
  h="${h%%:*}"
  printf '%s' "$h"
}

# assert_host HOST ALLOWLIST LABEL -> die 2 on a host outside the allowlist
assert_host() {
  case ",$2," in
    *",$1,"*) return 0 ;;
  esac
  die 2 "unexpected host '$1' for $3 — aborting (allowed: $2); nothing was extracted."
}

# fetch URL DEST ALLOWLIST LABEL TIMEOUT -> 0 ok, 2 failed (one retry, then fail)
fetch() {
  local url="$1" dest="$2" allowed="$3" label="$4" timeout="$5" attempt effective
  for attempt in 1 2; do
    if effective="$(curl -fsSL --max-time "$timeout" -w '%{url_effective}' -o "$dest" "$url" 2>/dev/null)"; then
      assert_host "$(host_of "$effective")" "$allowed" "$label"
      return 0
    fi
    if [ "$attempt" -eq 1 ]; then
      warn "fetch of $label failed — retrying once ..."
      sleep 2
    fi
  done
  return 2
}

# api_latest_tag -> the latest stable tag on stdout, or failure. Prints
# the tag ONLY (nothing else) so callers may capture it.
api_latest_tag() {
  local effective tag
  effective="$(curl -fsSL --max-time 20 -w '%{url_effective}' -o "$TMP_DIR/api.json" \
    "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null)" || return 1
  [ "$(host_of "$effective")" = "api.github.com" ] || return 1
  tag="$(sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$TMP_DIR/api.json")"
  tag="${tag%%$'\n'*}"
  [ -n "$tag" ] || return 1
  case "$tag" in latest|main|master) return 1 ;; esac
  printf '%s' "$tag"
}

# filename_tag -> the tag encoded in this file's own name, or empty.
# The exact-tag asset (<product>-bootstrap-vX.Y.Z.sh) knows its release.
filename_tag() {
  local base
  base="$(basename "$0")"
  case "$base" in
    "$PRODUCT-bootstrap-v"[0-9]*.[0-9]*.[0-9]*.sh)
      local tag="v${base#"$PRODUCT-bootstrap-v"}"
      printf '%s' "${tag%.sh}"
      ;;
  esac
}

# resolve_ref -> sets REF from --ref, this file's own name, or the GitHub
# API; never falls back to a branch.
REF=""
resolve_ref() {
  if [ "$EDGE_MODE" -eq 1 ]; then
    die 2 "--edge selects a branch, which is not a release — a branch has no release assets to verify. This bootstrap installs releases only; pass --ref vX.Y.Z."
  fi
  if [ -n "$REF_FLAG" ]; then
    case "$REF_FLAG" in
      latest|main|master)
        die 1 "refusing mutable ref '$REF_FLAG' — pass an explicit release tag (e.g. vX.Y.Z). The release alias resolves a stable tag itself."
        ;;
    esac
    REF="$REF_FLAG"
    info "Installing release: $REF (explicit)"
    return 0
  fi
  REF="$(filename_tag)"
  if [ -n "$REF" ]; then
    info "resolving the tag $REF from this file's name (no API call)."
    return 0
  fi
  info "Resolving the latest stable release ..."
  REF="$(api_latest_tag)" || die 2 "could not resolve the latest release of $REPO (GitHub API unreachable). This bootstrap never falls back to a branch — pass --ref vX.Y.Z or use --offline BUNDLE."
  info "Latest stable release: $REF"
}

# join_args ARGS... -> space-joined, for printing the hand-off command
join_args() {
  local out="" a
  for a in "$@"; do
    out="${out:+$out }$a"
  done
  printf '%s' "$out"
}

# hand_off ARGS... -> exec the bundle's installer from the install
# directory, reattaching the tty when this script was piped.
hand_off() {
  if [ ! -t 0 ] && { : < /dev/tty; } 2>/dev/null; then
    exec bash scripts/install.sh "$@" < /dev/tty
  fi
  exec bash scripts/install.sh "$@"
}

# ── parse flags ──────────────────────────────────────────────────────────────
DIR_FLAG=""; REF_FLAG=""; OFFLINE_FLAG=""
USER_IMAGES=(); FORWARD_ARGS=()
DRY_RUN=0; VERSION_MODE=0; ALLOW_ROOT=0; EDGE_MODE=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) print_help ;;
    --dir) [ "$#" -ge 2 ] || usage_error "--dir needs a value"; DIR_FLAG="$2"; shift ;;
    --ref) [ "$#" -ge 2 ] || usage_error "--ref needs a value"; REF_FLAG="$2"; shift ;;
    --offline) [ "$#" -ge 2 ] || usage_error "--offline needs a bundle path"; OFFLINE_FLAG="$2"; shift ;;
    --image) [ "$#" -ge 2 ] || usage_error "--image needs a value"; USER_IMAGES+=("$2"); shift ;;
    --dry-run) DRY_RUN=1 ;;
    --version) VERSION_MODE=1 ;;
    --edge) EDGE_MODE=1 ;;
    --allow-root) ALLOW_ROOT=1 ;;
    --) shift; FORWARD_ARGS=("$@"); break ;;
    -*) usage_error "unknown flag: $1" ;;
    *) usage_error "unexpected argument: $1 (flags only before --)" ;;
  esac
  shift
done

# flags win over the environment; the environment over the rendered default
DIR_FLAG="${DIR_FLAG:-$(env_opt INSTALL_DIR)}"
REF_FLAG="${REF_FLAG:-$(env_opt REF)}"
OFFLINE_FLAG="${OFFLINE_FLAG:-$(env_opt BUNDLE)}"
env_image="$(env_opt IMAGE)"
[ -z "$env_image" ] || USER_IMAGES+=("$env_image")
[ -z "$REF_FLAG" ] || [ "$EDGE_MODE" -eq 0 ] || usage_error "--ref and --edge are mutually exclusive"

# ── root refusal ─────────────────────────────────────────────────────────────
# --version may probe as root (it installs nothing); everything else may not.
if [ "${EUID:-$(id -u)}" -eq 0 ] && [ "$ALLOW_ROOT" -ne 1 ] && [ "$VERSION_MODE" -ne 1 ]; then
  die 1 "refusing to run as root — re-run as a normal user. --allow-root exists for CI/disposable containers only."
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# ── --version: the contract line first, then a best-effort release ───────────
if [ "$VERSION_MODE" -eq 1 ]; then
  printf 'bootstrap contract: %s\n' "$CONTRACT_VERSION"
  printf 'product: %s (%s)\n' "$PRODUCT" "$TITLE"
  if [ -n "$REF_FLAG" ]; then
    printf 'resolved release: %s (explicit)\n' "$REF_FLAG"
  elif [ -n "$OFFLINE_FLAG" ]; then
    printf 'resolved release: offline bundle %s\n' "$(basename "$OFFLINE_FLAG")"
  else
    pinned="$(filename_tag)"
    if [ -n "$pinned" ]; then
      printf 'resolved release: %s (pinned by this file'\''s name)\n' "$pinned"
    elif need_cmd curl && resolved="$(api_latest_tag)"; then
      printf 'resolved release: %s (latest stable)\n' "$resolved"
    else
      printf 'resolved release: unresolved (GitHub API unreachable; pass --ref)\n'
    fi
  fi
  exit 0
fi

# ── prerequisites: this script installs nothing, ever ────────────────────────
for cmd in curl tar; do
  need_cmd "$cmd" || die 1 "'$cmd' is required but not installed."
done
if ! need_cmd sha256sum && ! need_cmd shasum; then
  die 1 "sha256sum (or 'shasum -a 256') is required but not installed."
fi
if ! need_cmd docker; then
  die 1 "Docker is required before this bootstrap can continue.
  Install it for your platform: https://docs.docker.com/engine/install/
  (This script deliberately installs no system packages for you.)"
fi

# RAM hint (never a hard fail; containers may not expose meminfo)
if [ -r /proc/meminfo ]; then
  RAM_MB="$(awk '/^MemTotal:/ {printf "%d", $2 / 1024; exit}' /proc/meminfo)"
  if [ -n "${RAM_MB:-}" ] && [ "$RAM_MB" -lt $((MIN_RAM_GB * 1024)) ]; then
    warn "host RAM is ~${RAM_MB}MB; $TITLE wants ${MIN_RAM_GB}GB — the stack may struggle."
  fi
fi

if [ -f "$0" ] && [ -r "$0" ]; then
  info "This bootstrap: $(basename "$0") (SHA-256 $(sha256_of "$0"))"
else
  info "Piped bootstrap — to verify it, download it first and compare its SHA-256 against the release's SHA256SUMS."
fi

# ── target directory ─────────────────────────────────────────────────────────
DIR="${DIR_FLAG:-$DEFAULT_DIR}"
DIR="${DIR/#\~/$HOME}"
DIR="${DIR%/}"

# ── existing install: hand off, download nothing, never touch .env ──────────
# A user --image and forwarded flags go to the existing installer too: an
# old tree that does not understand them fails loudly, never silently.
if [ -d "$DIR" ] && [ -f "$DIR/scripts/install.sh" ]; then
  info "Existing install found in $DIR — re-running its own installer (nothing downloaded, .env untouched)."
  EXISTING_ARGS=()
  for img in ${USER_IMAGES[@]+"${USER_IMAGES[@]}"}; do
    EXISTING_ARGS+=(--image "$img")
  done
  EXISTING_ARGS+=(${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"})
  if [ "$DRY_RUN" -eq 1 ]; then
    info "Dry run OK — the existing installer would run next:"
    printf '  Hand-off:   cd %s && bash scripts/install.sh %s\n' \
      "$DIR" "$(join_args ${EXISTING_ARGS[@]+"${EXISTING_ARGS[@]}"})"
    exit 0
  fi
  cd "$DIR"
  info "Handing off to the existing install's own installer (interactive questions go to your terminal)."
  hand_off ${EXISTING_ARGS[@]+"${EXISTING_ARGS[@]}"}
fi

# A non-empty directory without an installer is user data — refuse, do not touch.
if [ -d "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
  die 1 "target directory '$DIR' already exists and is not empty, and holds no installer to re-run.
    Move it aside, then re-run this command:  mv '$DIR' '${DIR}.bak'
    Or choose another location:              --dir /new/path"
fi

# ── resolve the release and locate the bundle ────────────────────────────────
BUNDLE_FILE=""; BUNDLE_NAME=""; SUMS_FILE=""; MANIFEST_FILE=""
RELEASE_DESC=""; BUNDLE_DESC=""

if [ -n "$OFFLINE_FLAG" ]; then
  [ -f "$OFFLINE_FLAG" ] || die 2 "--offline bundle not found: $OFFLINE_FLAG"
  info "Offline mode: using $OFFLINE_FLAG (SHA256SUMS beside it)."
  BUNDLE_FILE="$OFFLINE_FLAG"
  BUNDLE_NAME="$(basename "$OFFLINE_FLAG")"
  SUMS_FILE="$(dirname "$OFFLINE_FLAG")/SHA256SUMS"
  MANIFEST_FILE="$(dirname "$OFFLINE_FLAG")/manifest.json"
  RELEASE_DESC="offline bundle"
  BUNDLE_DESC="$OFFLINE_FLAG"
  if [ ! -f "$MANIFEST_FILE" ]; then
    warn "no manifest.json beside the offline bundle — no image pins will be forwarded."
    MANIFEST_FILE=""
  fi
  [ -z "$REF_FLAG" ] || warn "--ref is ignored in --offline mode."
else
  resolve_ref
  info "Fetching the release assets of $REF ..."
  base_url="https://github.com/$REPO/releases/download/$REF"
  BUNDLE_NAME="${BUNDLE_PREFIX}-${REF}.tar.gz"
  RELEASE_DESC="$REF"
  BUNDLE_DESC="$base_url/$BUNDLE_NAME"
  fetch "$base_url/$BUNDLE_NAME" "$TMP_DIR/$BUNDLE_NAME" \
      "github.com,objects.githubusercontent.com" "the runtime bundle" 180 \
    || die 2 "fetch failed: $BUNDLE_DESC — check the ref '$REF' and your network."
  fetch "$base_url/manifest.json" "$TMP_DIR/manifest.json" \
      "github.com,objects.githubusercontent.com" "the release manifest" 60 \
    || die 2 "fetch failed: $base_url/manifest.json — check the ref '$REF' and your network."
  fetch "$base_url/SHA256SUMS" "$TMP_DIR/SHA256SUMS" \
      "github.com,objects.githubusercontent.com" "SHA256SUMS" 60 \
    || die 2 "fetch failed: $base_url/SHA256SUMS — check the ref '$REF' and your network."
  BUNDLE_FILE="$TMP_DIR/$BUNDLE_NAME"
  MANIFEST_FILE="$TMP_DIR/manifest.json"
  SUMS_FILE="$TMP_DIR/SHA256SUMS"
fi

# ── verify BEFORE extraction: fail closed ────────────────────────────────────
EXPECTED_SHA="$(env_opt EXPECTED_SHA256)"
if [ ! -f "$SUMS_FILE" ] && [ -z "$EXPECTED_SHA" ]; then
  die 2 "no SHA256SUMS beside '$BUNDLE_NAME' and ${ENV_PREFIX}_EXPECTED_SHA256 is unset — refusing to install an unverified bundle."
fi

ACTUAL_SHA="$(sha256_of "$BUNDLE_FILE")"
info "Bundle SHA-256: $ACTUAL_SHA"

if [ -f "$SUMS_FILE" ]; then
  sums_bundle_sha="$(awk -v f="$BUNDLE_NAME" '$2 == f || $2 == "./" f {print $1; exit}' "$SUMS_FILE")"
  [ -n "$sums_bundle_sha" ] \
    || die 2 "SHA256SUMS has no entry for '$BUNDLE_NAME' — refusing to install an unverified bundle."
  [ "$ACTUAL_SHA" = "$sums_bundle_sha" ] \
    || die 2 "SHA-256 mismatch for '$BUNDLE_NAME' (SHA256SUMS says $sums_bundle_sha) — aborting; nothing was extracted."
  if [ -n "$MANIFEST_FILE" ] && awk '$2 == "manifest.json" || $2 == "./manifest.json" {found = 1} END {exit !found}' "$SUMS_FILE"; then
    sums_manifest_sha="$(awk '$2 == "manifest.json" || $2 == "./manifest.json" {print $1; exit}' "$SUMS_FILE")"
    [ "$(sha256_of "$MANIFEST_FILE")" = "$sums_manifest_sha" ] \
      || die 2 "SHA-256 mismatch for manifest.json — aborting; nothing was extracted."
  fi
fi
if [ -n "$EXPECTED_SHA" ]; then
  [ "$ACTUAL_SHA" = "$EXPECTED_SHA" ] \
    || die 2 "SHA-256 mismatch (expected $EXPECTED_SHA via ${ENV_PREFIX}_EXPECTED_SHA256) — aborting; nothing was extracted."
fi

# ── manifest: product, version and image pins to hand off ───────────────────
MANIFEST_PINS=()
if [ -n "$MANIFEST_FILE" ]; then
  manifest_product="$(sed -n 's/.*"product"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$MANIFEST_FILE")"
  manifest_product="${manifest_product%%$'\n'*}"
  if [ -n "$manifest_product" ] && [ "$manifest_product" != "$PRODUCT" ]; then
    die 2 "manifest product '$manifest_product' does not match this bootstrap (expected '$PRODUCT') — aborting; nothing was extracted."
  fi
  manifest_version="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$MANIFEST_FILE")"
  manifest_version="${manifest_version%%$'\n'*}"
  [ -z "$manifest_version" ] || RELEASE_DESC="$RELEASE_DESC (manifest version $manifest_version)"
  while IFS= read -r pair; do
    [ -n "$pair" ] || continue
    pin_key="${pair%%=*}"
    pin_ref="${pair#*=}"
    case " $IMAGE_KEYS " in
      *" $pin_key "*) ;;
      *) die 2 "manifest image key '$pin_key' is not one of this product's image keys ($IMAGE_KEYS) — aborting; nothing was extracted." ;;
    esac
    case "$pin_ref" in
      *":latest"|*":main")
        die 2 "manifest image '$pin_key' is not pinned ('$pin_ref') — refusing; nothing was extracted."
        ;;
    esac
    MANIFEST_PINS+=("$pair")
  done < <(sed -n '/"images"[[:space:]]*:[[:space:]]*{/,/}/p' "$MANIFEST_FILE" \
    | sed -n 's/^[[:space:]]*"\([^"]*\)"[[:space:]]*:[[:space:]]*"\([^"]*\)".*$/\1=\2/p')
fi

# ── the bundle must be a contract bundle before anything is extracted ───────
LISTING="$(tar -tzf "$BUNDLE_FILE")" \
  || die 2 "could not list the bundle (corrupt or incomplete archive?) — aborting."
grep -Eq '^(\./)?scripts/install\.sh$' <<<"$LISTING" \
  || die 2 "the bundle does not contain scripts/install.sh — not a contract bundle; aborting."
grep -Eq '^(\./)?installer\.json$' <<<"$LISTING" \
  || die 2 "the bundle does not contain installer.json — not a contract bundle; aborting."

# ── the hand-off argument list: manifest pins, then user overrides, then -- ──
INSTALL_ARGS=()
for pin in ${MANIFEST_PINS[@]+"${MANIFEST_PINS[@]}"}; do
  INSTALL_ARGS+=(--image "$pin")
done
for img in ${USER_IMAGES[@]+"${USER_IMAGES[@]}"}; do
  INSTALL_ARGS+=(--image "$img")
done
INSTALL_ARGS+=(${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"})

# ── dry run: everything above is verified; write nothing outside TMP_DIR ────
if [ "$DRY_RUN" -eq 1 ]; then
  printf '  Release:    %s\n' "$RELEASE_DESC"
  printf '  Bundle:     %s\n' "$BUNDLE_DESC"
  printf '  SHA-256:    %s\n' "$ACTUAL_SHA"
  printf '  Images:     %s\n' "$(join_args ${MANIFEST_PINS[@]+"${MANIFEST_PINS[@]}"})"
  printf '  Target dir: %s\n' "$DIR"
  info "Dry run OK — nothing was written to the target directory."
  printf '  Hand-off:   cd %s && bash scripts/install.sh %s\n' \
    "$DIR" "$(join_args ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"})"
  exit 0
fi

# ── extract and hand off ─────────────────────────────────────────────────────
mkdir -p "$DIR"
info "Extracting into $DIR ..."
tar -xzf "$BUNDLE_FILE" -C "$DIR" \
  || die 2 "extraction failed — the bundle may be corrupt or incomplete."
[ -f "$DIR/scripts/install.sh" ] \
  || die 2 "extracted bundle is incomplete (scripts/install.sh missing) — aborting."
# The bundle installer reads manifest.json for the release version and the
# image repo. It is a release asset (not inside the tarball), already
# verified above; place the verified copy beside the extracted bundle so the
# hand-off is self-contained.
if [ -n "$MANIFEST_FILE" ] && [ -f "$MANIFEST_FILE" ]; then
  cp "$MANIFEST_FILE" "$DIR/manifest.json" \
    || die 2 "could not place manifest.json in $DIR — aborting."
fi

if [ "${#MANIFEST_PINS[@]}" -gt 0 ]; then
  info "Images pinned from the manifest: $(join_args ${MANIFEST_PINS[@]+"${MANIFEST_PINS[@]}"})"
fi

cd "$DIR"
info "Handing off to the bundle's own installer (interactive questions go to your terminal)."
hand_off ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"}
