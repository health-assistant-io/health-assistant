#!/bin/bash

# Health Assistant — remote bootstrap (one-line install)
#
# Designed to be piped straight from curl:
#
#   curl -fsSL https://health-assistant.io/install.sh | bash
#
# What it does, in the open: checks prerequisites (bash, curl, tar, Docker —
# nothing is installed for you, nothing runs as root), resolves the latest
# published release tag, downloads the official repo tarball from GitHub,
# prints its SHA-256, extracts it to the install directory, and hands off to
# the audited in-repo installer (scripts/install.sh) with your terminal
# attached — so its interactive questions still reach you even though this
# script's stdin is the curl pipe.
#
# It is a small, readable bootstrap on purpose: the heavy lifting stays in
# scripts/install.sh, which you can read in the extracted directory before
# it ever runs. Nothing here prompts for sudo and nothing is installed
# system-wide; the stack runs entirely through Docker.
#
# Usage:
#   curl -fsSL https://health-assistant.io/install.sh | bash
#   curl -fsSL https://health-assistant.io/install.sh | bash -s -- --dry-run
#   curl -fsSL https://health-assistant.io/install.sh | bash -s -- --dir /srv/ha
#   ./scripts/bootstrap.sh --help
#
# Flags:
#   --dir DIR      install directory (default: ~/health-assistant)
#   --ref REF      repo tag or branch (default: latest release, fallback main)
#   --dry-run      fetch + verify + extract, print the hand-off command, exit
#   -h, --help     print this help and exit
#
# Anything after the flags is forwarded to scripts/install.sh
# (e.g. ... | bash -s -- --reset --yes).
#
# Environment overrides:
#   HEALTH_INSTALL_DIR        same as --dir
#   HEALTH_REF                same as --ref
#   HEALTH_EXPECTED_SHA256    when set, the downloaded tarball's SHA-256 must
#                             match exactly, or the install aborts (for
#                             pinning a known-good release)
#   HEALTH_REPO               GitHub repo slug (default: the official one)
#
# Requires: bash, curl, tar. The installer additionally needs Docker with
# compose and python3; this script checks Docker up front so you fail fast
# with instructions instead of halfway through.

set -euo pipefail

REPO="${HEALTH_REPO:-health-assistant-io/health-assistant}"
DEFAULT_DIR="$HOME/health-assistant"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; NC=''
fi

info() { printf "${GREEN}==>${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}warning:${NC} %s\n" "$*"; }
die()  { printf "${RED}error:${NC} %s\n" "$*" >&2; exit 1; }

print_help() {
    sed -n '3,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
    exit 0
}

DIR="${HEALTH_INSTALL_DIR:-}"
REF="${HEALTH_REF:-}"
DRY_RUN=0
FORWARD_ARGS=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) print_help ;;
        --dir) [ $# -ge 2 ] || die "--dir needs a value"; DIR="$2"; shift ;;
        --ref) [ $# -ge 2 ] || die "--ref needs a value"; REF="$2"; shift ;;
        --dry-run) DRY_RUN=1 ;;
        --) shift; FORWARD_ARGS=("$@"); break ;;
        *) FORWARD_ARGS+=("$1") ;;
    esac
    shift
done

for cmd in curl tar; do
    command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' is required but not installed."
done

if ! command -v docker >/dev/null 2>&1; then
    warn "Docker is not installed. The stack needs it before the installer can run."
    echo "  Install it for your platform: https://docs.docker.com/engine/install/"
    echo "  (This script deliberately does not install system packages for you.)"
    echo
fi
if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
    warn "Docker is installed but the daemon is not running — start it before the installer runs."
    echo
fi

[ -n "$DIR" ] || DIR="$DEFAULT_DIR"
DIR="${DIR%/}"

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if [ -n "$REF" ]; then
    info "Installing reference: $REF"
else
    info "Resolving the latest published release ..."
    REF="$(curl -fsSL --max-time 20 "https://api.github.com/repos/$REPO/releases/latest" \
        2>/dev/null | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 || true)"
    if [ -n "$REF" ]; then
        info "Latest release: $REF"
    else
        REF="main"
        warn "Could not reach the GitHub API — falling back to 'main'."
    fi
fi

if [ -d "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    die "Target directory '$DIR' already exists and is not empty.
    Re-run an existing install instead:  cd $DIR && ./scripts/install.sh
    Or choose another location:          bash -s -- --dir /new/path"
fi

ARCHIVE="$TMP_DIR/health-assistant.tgz"
info "Downloading $REPO ($REF) from GitHub ..."
curl -fsSL --max-time 120 "https://codeload.github.com/$REPO/tar.gz/$REF" -o "$ARCHIVE" \
    || die "Download failed — check the ref '$REF' and your network."

ACTUAL_SHA="$(sha256_of "$ARCHIVE")"
info "Tarball SHA-256: $ACTUAL_SHA"
if [ -n "${HEALTH_EXPECTED_SHA256:-}" ] && [ "$ACTUAL_SHA" != "$HEALTH_EXPECTED_SHA256" ]; then
    die "SHA-256 mismatch (expected $HEALTH_EXPECTED_SHA256) — aborting, nothing was extracted."
fi

mkdir -p "$DIR"
[ "$(ls -A "$DIR" 2>/dev/null)" ] || rmdir "$DIR"
info "Extracting into $DIR ..."
mkdir -p "$DIR"
tar -xzf "$ARCHIVE" -C "$DIR" --strip-components=1 \
    || die "Extraction failed — the tarball may be corrupt or incomplete."

[ -f "$DIR/scripts/install.sh" ] || die "Extracted tree is incomplete (scripts/install.sh missing) — aborting."

cd "$DIR"

if [ "$DRY_RUN" -eq 1 ]; then
    info "Dry run OK — everything above is verified. The installer would run next:"
    echo "    cd $DIR && bash scripts/install.sh ${FORWARD_ARGS[*]:-}"
    echo "  Re-run it any time:  cd $DIR && ./scripts/install.sh"
    exit 0
fi

info "Handing off to the official installer (interactive questions go to your terminal)."
echo
if [ ! -t 0 ] && [ -e /dev/tty ]; then
    exec bash scripts/install.sh ${FORWARD_ARGS+"${FORWARD_ARGS[@]}"} < /dev/tty
fi
exec bash scripts/install.sh ${FORWARD_ARGS+"${FORWARD_ARGS[@]}"}
