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
# The extracted tree and the published images must describe the SAME contract,
# so the hand-off passes `--image-tag <release>`: image tags come from the
# release tag (`v0.9.0` → `0.9.0`), while `latest` is built from main. Without
# that pin a release install runs main's images and dies at the migrate step
# (env vars renamed, "no password supplied").
#
# Re-running against an existing install does not download or overwrite
# anything: it finds <dir>/scripts/install.sh and runs it instead (which is
# idempotent — it keeps .env, pulls, `up -d`, waits for the health check). A
# tarball install that predates image pinning gets `IMAGE_TAG` pinned for that
# run (and saved in its .env) so a re-run repairs it.
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

# The published image tag that matches a ref: the publish workflow tags release
# builds X.Y.Z (docker-publish.yml `type=semver`) and branch builds by branch
# name, and `latest` only from the default branch. Anything else has no image
# of its own — return nothing so the caller warns instead of inventing a tag.
image_tag_for_ref() {
    case "$1" in
        v[0-9]*.[0-9]*.[0-9]*) printf '%s' "${1#v}" ;;
        [0-9]*.[0-9]*.[0-9]*) printf '%s' "$1" ;;
        main|master) printf '%s' "$1" ;;
        *) printf '' ;;
    esac
}

# The version an existing install's tree declares (same source of truth as
# scripts/lib-docker.sh tree_version — this script stays standalone).
installed_version() { # DIR
    local CFG="$1/backend/app/core/config.py"
    [ -f "$CFG" ] || return 1
    sed -n 's/^[[:space:]]*VERSION:[[:space:]]*str[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$CFG" | head -n1
}

# Persist IMAGE_TAG in an existing install's .env (idempotent; leaves every
# other line alone). Only used for tarball installs whose own installer
# predates --image-tag.
pin_env_file() { # DIR TAG
    local ENVF="$1/.env" TMP
    [ -f "$ENVF" ] || return 1
    TMP="$(mktemp)"
    awk -v t="$2" '
        /^IMAGE_TAG=/ { print "IMAGE_TAG=" t; seen = 1; next }
        { print }
        END { if (!seen) print "IMAGE_TAG=" t }
    ' "$ENVF" > "$TMP" || { rm -f "$TMP"; return 1; }
    cat "$TMP" > "$ENVF"
    rm -f "$TMP"
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

PIN="$(image_tag_for_ref "$REF")"
PIN_ARGS=""
if [ -n "$PIN" ]; then
    PIN_ARGS="--image-tag $PIN"
else
    warn "Ref '$REF' has no published image tag — images fall back to :latest, which may not match this tree."
fi

# Existing install: never overwrite it, run its own installer instead (keeps
# .env, pulls, `up -d`, waits for health). Release trees that predate image
# pinning get the tag pinned so a re-run repairs the migrate failure.
if [ -d "$DIR" ] && [ -f "$DIR/scripts/install.sh" ]; then
    info "Existing install found in $DIR — re-running its installer (nothing downloaded)."
    if [ -n "$PIN" ] && [ ! -d "$DIR/.git" ]; then
        TREE_TAG="$(installed_version "$DIR")"
        if [[ "$TREE_TAG" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]]; then
            if [ "$TREE_TAG" != "$PIN" ]; then
                warn "Installed tree is ${TREE_TAG}, latest release is ${REF} — pinning its own images; update later with ./scripts/update-docker.sh."
            fi
            if pin_env_file "$DIR" "$TREE_TAG"; then
                info "Pinned IMAGE_TAG=${TREE_TAG} in $DIR/.env (matches the installed tree)."
            fi
            export IMAGE_TAG="$TREE_TAG"
        else
            warn "Could not read the installed tree's version — leaving IMAGE_TAG as it is."
        fi
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        info "Dry run OK — the installer would run next:"
        echo "    cd $DIR && bash scripts/install.sh ${FORWARD_ARGS[*]:-}"
        exit 0
    fi
    cd "$DIR"
    if [ ! -t 0 ] && { : < /dev/tty; } 2>/dev/null; then
        exec bash scripts/install.sh ${FORWARD_ARGS+"${FORWARD_ARGS[@]}"} < /dev/tty
    fi
    exec bash scripts/install.sh ${FORWARD_ARGS+"${FORWARD_ARGS[@]}"}
fi

if [ -d "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    die "Target directory '$DIR' already exists and is not empty, and holds no installer to re-run.
    Move it aside, then re-run this command:
      mv $DIR ${DIR}.bak
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
    echo "    cd $DIR && bash scripts/install.sh $PIN_ARGS ${FORWARD_ARGS[*]:-}"
    echo "  Re-run it any time:  cd $DIR && ./scripts/install.sh"
    exit 0
fi

info "Handing off to the official installer (interactive questions go to your terminal)."
if [ -n "$PIN" ]; then
    info "Images pinned to $PIN (saved in .env by the installer)."
fi
echo
if [ ! -t 0 ] && { : < /dev/tty; } 2>/dev/null; then
    exec bash scripts/install.sh $PIN_ARGS ${FORWARD_ARGS+"${FORWARD_ARGS[@]}"} < /dev/tty
fi
exec bash scripts/install.sh $PIN_ARGS ${FORWARD_ARGS+"${FORWARD_ARGS[@]}"}
