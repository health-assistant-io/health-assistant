#!/bin/bash

# Health Assistant — bundle updater (the family installer contract)
#
# Updates an install made by scripts/install.sh from a NEW source-free
# release bundle (extracted over the existing install directory — the bundle
# carries no .env, so .env and its secrets survive):
#   records and keeps the previous image pins → bumps
#   HA_BACKEND_IMAGE / HA_FRONTEND_IMAGE (per-slot --image KEY=REF flags,
#   --image-tag, or the manifest.json shipped beside the new bundle) →
#   pull → up -d → wait for /health and /ready.
#
# Usage (from the new bundle's root, over the existing install):
#   bash scripts/update.sh                     # pins from manifest.json when present
#   bash scripts/update.sh --image backend=ghcr.io/…@sha256:… \
#                           --image frontend=ghcr.io/…@sha256:…   # pin-passing form
#   bash scripts/update.sh --image-tag 0.10.4  # pin to a release tag
#   bash scripts/update.sh --no-pull           # reuse local images
#   bash scripts/update.sh --no-wait           # skip the health/readiness wait
#   bash scripts/update.sh -h|--help           # this help
#
# No git, no destructive data operations: this script never removes volumes
# and never touches secrets in .env (the only writes are the image pins and
# a comment recording the previous ones). Take a backup first —
# scripts/backup.sh — pre-1.0 schema migrations have no downgrade path.
#
# Rollback: the previous pins are printed and kept as comments in .env —
# restore them with:
#   bash scripts/update.sh --image backend=<previous pin> \
#                           --image frontend=<previous pin>
# and restore data from the pre-upgrade backup when needed.
#
# Exit codes (the installer contract): 0 ok · 1 usage/prerequisite ·
# 2 image pull · 3 compose start or health-wait · 4 port conflict.

print_help() {
  sed -n '3,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit 0
}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib-docker.sh"

# ── flags ───────────────────────────────────────────────────────────────────
NO_PULL=0 NO_WAIT=0
IMAGE_TAG_ARG="" ALLOW_ROOT="${ALLOW_ROOT:-}"
IMAGE_ARGS=()   # --image values (KEY=REF), resolved after the config load
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) print_help ;;
        --no-pull) NO_PULL=1 ;;
        --no-wait) NO_WAIT=1 ;;
        --allow-root) ALLOW_ROOT=1 ;;
        --image)
            [ "$#" -ge 2 ] || die "--image needs a value (KEY=REF)"
            IMAGE_ARGS+=("$2"); shift ;;
        --image-tag)
            [ "$#" -ge 2 ] || die "--image-tag needs a value"
            IMAGE_TAG_ARG="$2"; shift ;;
        *)
            echo -e "${RED}Unknown parameter: $1 (try --help)${NC}" >&2
            exit 1
            ;;
    esac
    shift
done

# ── pre-flight ────────────────────────────────────────────────────────────────
if [ "$(id -u)" = "0" ] && [ -z "$ALLOW_ROOT" ]; then
    echo -e "${RED}Refusing to run as root (the installer contract: no root, no sudo).${NC}" >&2
    echo "  Re-run as your normal account; CI/disposable containers only: --allow-root" >&2
    exit 1
fi
[ -n "$ALLOW_ROOT" ] && echo -e "${YELLOW}--allow-root: continuing as root (CI/disposable use only).${NC}"

check_cwd
check_docker
[ "$DOCKER_COMPOSE_CMD" = "docker compose" ] \
    || die "Docker Compose v2 ('docker compose') is required; legacy docker-compose v1 cannot run the standalone stack."
[ -f "$ENV_FILE" ] \
    || die "${ENV_FILE} not found — this is not an existing install. Run scripts/install.sh first."

# ── product config (installer.json ships with the bundle) ───────────────────
HEALTH_PATH="$(json_field installer.json healthPath)"; [ -n "$HEALTH_PATH" ] || HEALTH_PATH="/health"
READY_PATH="$(json_field installer.json readyPath)"
IMAGE_ENV="$(json_field installer.json imageEnv)"; [ -n "$IMAGE_ENV" ] || IMAGE_ENV="IMAGE_TAG"
IMAGE_KEYS="$(json_keys installer.json imageKeys)"
if [ -z "$IMAGE_KEYS" ]; then
    echo -e "${YELLOW}Could not read imageKeys from installer.json — accepting 'backend frontend'.${NC}"
    IMAGE_KEYS="backend frontend"
fi
MANIFEST_VERSION="$(json_field manifest.json version)"

HTTP_PORT="$(env_value HTTP_PORT 80)"
HEALTH_URL="http://127.0.0.1:${HTTP_PORT}${HEALTH_PATH}"
READY_URL="http://127.0.0.1:${HTTP_PORT}${READY_PATH}"
# The readiness probe carries APP_URL's hostname: production's
# TrustedHostMiddleware rejects a bare 127.0.0.1 Host header with 400.
READY_HOST="$(printf '%s' "$(env_value APP_URL '')" | sed -E 's#^[a-z]+://([^:/]+).*#\1#')"
[ -n "$READY_HOST" ] || READY_HOST="127.0.0.1"

# ── the new pins (--image KEY=REF > --image-tag > the new bundle's
#    manifest.json > keep the current pins) ──────────────────────────────────
manifest_pin() { json_field manifest.json "images.$1"; }

image_repo_hint() { # repository part of a ref — derived, never hardcoded
    case "$1" in
        *@sha256:*) printf '%s' "${1%%@*}" ;;
        *:*)       printf '%s' "${1%%:*}" ;;
        *)         printf '%s' "$1" ;;
    esac
}

default_repo() { # slot — the compose files' own default repo parts (.env-overridable)
    local REG REPO
    REG="$(env_value REGISTRY "ghcr.io")"
    REPO="$(env_value REPOSITORY "health-assistant-io/health-assistant")"
    printf '%s/%s/health-assistant-%s' "$REG" "$REPO" "$1"
}

NEW_BACKEND_PIN="" NEW_FRONTEND_PIN="" OPERATOR_TYPED=0
for ARG in ${IMAGE_ARGS[@]+"${IMAGE_ARGS[@]}"}; do
    KEY="" REF="$ARG"
    case "$ARG" in
        *=*) KEY="${ARG%%=*}"; REF="${ARG#*=}" ;;
        *)
            die "refusing the bare image ref '$ARG' — this product has two image slots (${IMAGE_KEYS}). Name the slot: --image backend=<ref> --image frontend=<ref>."
            ;;
    esac
    case " $IMAGE_KEYS " in
        *" $KEY "*) ;;
        *) die "unknown image key '$KEY' in --image '$ARG' — this product's imageKeys: ${IMAGE_KEYS}" ;;
    esac
    case "$KEY" in
        backend)  NEW_BACKEND_PIN="$REF" ;;
        frontend) NEW_FRONTEND_PIN="$REF" ;;
    esac
    OPERATOR_TYPED=1
done

if [ -z "$NEW_BACKEND_PIN$NEW_FRONTEND_PIN" ] && [ -n "$IMAGE_TAG_ARG" ]; then
    TAG="${IMAGE_TAG_ARG#v}"
    case "$TAG" in
        [0-9]*.[0-9]*.[0-9]*) ;;
        *) die "--image-tag must look like X.Y.Z or vX.Y.Z (got: $IMAGE_TAG_ARG)" ;;
    esac
    for SLOT in $IMAGE_KEYS; do
        REPO_SRC="$(manifest_pin "$SLOT")"
        [ -z "$REPO_SRC" ] && REPO_SRC="$(env_value "HA_$(echo "$SLOT" | tr '[:lower:]' '[:upper:]')_IMAGE" '')"
        [ -z "$REPO_SRC" ] && REPO_SRC="$(default_repo "$SLOT")"
        if [ "$SLOT" = "backend" ]; then
            NEW_BACKEND_PIN="$(image_repo_hint "$REPO_SRC"):${TAG}"
        else
            NEW_FRONTEND_PIN="$(image_repo_hint "$REPO_SRC"):${TAG}"
        fi
    done
    OPERATOR_TYPED=1
fi

[ -z "$NEW_BACKEND_PIN" ]  && NEW_BACKEND_PIN="$(manifest_pin backend)"
[ -z "$NEW_FRONTEND_PIN" ] && NEW_FRONTEND_PIN="$(manifest_pin frontend)"

# Operator-typed mutable pins are refused here; manifest-sourced pins are
# checked against the rendered compose model below (exit 3).
if [ "$OPERATOR_TYPED" -eq 1 ]; then
    for PIN in "$NEW_BACKEND_PIN" "$NEW_FRONTEND_PIN"; do
        [ -n "$PIN" ] || continue
        case "$PIN" in
            *:latest|*:main)
                die "Refusing the mutable pin '$PIN' — latest/main float with the develop branch and can break the migrate step. The installer contract pins releases only; pass a release tag or a digest."
                ;;
        esac
    done
fi

# Release-skew guard: a hand-passed --image-tag must agree with the new
# bundle's manifest.
if [ -n "$IMAGE_TAG_ARG" ] && [ -n "$MANIFEST_VERSION" ]; then
    MV="${MANIFEST_VERSION#v}"
    [ "$MV" = "${IMAGE_TAG_ARG#v}" ] \
        || die "--image-tag ${IMAGE_TAG_ARG} does not match this bundle's manifest (version ${MV}). Pin the matching release, or drop --image-tag and let the manifest pin it."
fi

echo -e "${YELLOW}Take a backup before moving the pin: bash scripts/backup.sh${NC}"

# ── move the pins, keeping the previous ones for rollback ────────────────────
CURRENT_BACKEND_PIN="$(env_value HA_BACKEND_IMAGE '')"
CURRENT_FRONTEND_PIN="$(env_value HA_FRONTEND_IMAGE '')"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

move_pin() { # ENV_VAR NEW_PIN CURRENT_PIN LABEL
    local VAR="$1" NEW="$2" OLD="$3" LABEL="$4"
    if [ -z "$NEW" ]; then
        return 0
    fi
    if [ "$NEW" = "$OLD" ]; then
        echo -e "${GREEN}Keeping the current ${LABEL} pin: ${OLD}${NC}"
        return 0
    fi
    # Keep the previous pin (rollback): a comment in .env (compose ignores
    # comments) plus the printed rollback command.
    if [ -n "$OLD" ]; then
        printf '# previous %s (kept by scripts/update.sh %s): %s\n' \
            "$VAR" "$STAMP" "$OLD" >> "$ENV_FILE"
    fi
    set_env_value "$VAR" "$NEW"
    echo -e "${GREEN}${LABEL} pin moved: ${OLD:-<none>} → ${NEW}${NC}"
}

PINS_MOVED=0
if [ -n "$NEW_BACKEND_PIN" ] || [ -n "$NEW_FRONTEND_PIN" ]; then
    if [ -n "$NEW_BACKEND_PIN" ] && [ "$NEW_BACKEND_PIN" != "$CURRENT_BACKEND_PIN" ]; then
        PINS_MOVED=1
        move_pin HA_BACKEND_IMAGE "$NEW_BACKEND_PIN" "$CURRENT_BACKEND_PIN" "backend"
    fi
    if [ -n "$NEW_FRONTEND_PIN" ] && [ "$NEW_FRONTEND_PIN" != "$CURRENT_FRONTEND_PIN" ]; then
        PINS_MOVED=1
        move_pin HA_FRONTEND_IMAGE "$NEW_FRONTEND_PIN" "$CURRENT_FRONTEND_PIN" "frontend"
    fi
    # Record the release tag for the operator (the IMAGE_TAG seam). For a
    # --image-tag run this is the tag itself; for digest pins it is the tag
    # the ref carries.
    TAGPART=""
    if [ -n "$IMAGE_TAG_ARG" ]; then
        TAGPART="${IMAGE_TAG_ARG#v}"
    else
        case "$NEW_BACKEND_PIN" in
            *@sha256:*) TAGPART="${NEW_BACKEND_PIN%%@*}"; TAGPART="${TAGPART##*:}" ;;
        esac
    fi
    if [ -n "$TAGPART" ] && [ "$TAGPART" != "$(env_value "$IMAGE_ENV" '')" ]; then
        set_env_value "$IMAGE_ENV" "$TAGPART"
    fi
    if [ "$PINS_MOVED" -eq 1 ] && [ -n "$CURRENT_BACKEND_PIN$CURRENT_FRONTEND_PIN" ]; then
        echo -e "${YELLOW}Rollback (previous pins kept in ${ENV_FILE}):${NC}"
        echo "  bash scripts/update.sh --image backend=${CURRENT_BACKEND_PIN:-<previous pin>} --image frontend=${CURRENT_FRONTEND_PIN:-<previous pin>}"
    fi
else
    echo -e "${GREEN}No new pin found — refreshing the current release.${NC}"
fi

BACKEND_PIN="$(env_value HA_BACKEND_IMAGE '')"
FRONTEND_PIN="$(env_value HA_FRONTEND_IMAGE '')"

# ── upgrade pre-flight (read-only): refuse a pre-0.9 database early ──────────
run_upgrade_preflight

# Adopt volumes from before the compose files pinned their project name — a
# no-op on every install made since the pin.
adopt_legacy_volumes

# ── port pre-flight ──────────────────────────────────────────────────────────
# The running stack's own ports pass; a foreign holder of the port means
# something else grabbed it — stop before touching images.
check_published_ports

# ── image verification ───────────────────────────────────────────────────────
# Rendered compose model vs the pins; mutable refs never reach up -d.
# Exit 3 (fixtures manifest-image-compose-assert / mutable-compose-ref-refused).
assert_rendered_image() {
    local RENDERED SLOT IMG EXPECTED
    RENDERED="$(run_compose config --format json 2>/dev/null | python3 -c '
import json, sys
try:
    model = json.load(sys.stdin)
except Exception:
    sys.exit(1)
slots = {
    "backend": ("migrate", "backend", "worker", "beat", "flower"),
    "frontend": ("frontend",),
}
for slot, services in slots.items():
    for name in services:
        svc = (model.get("services") or {}).get(name) or {}
        print("%s\t%s" % (slot, svc.get("image") or ""))
' 2>/dev/null || true)"
    [ -n "$RENDERED" ] || return 0
    while IFS=$'\t' read -r SLOT IMG; do
        [ -n "$IMG" ] || continue
        EXPECTED=""
        [ "$SLOT" = "backend" ] && EXPECTED="$BACKEND_PIN"
        [ "$SLOT" = "frontend" ] && EXPECTED="$FRONTEND_PIN"
        case "$IMG" in
            *:latest|*:main)
                echo -e "${RED}Mutable image ref refused: compose resolved '${IMG}' — latest/main never reach rendered Compose (the installer contract pins releases only).${NC}" >&2
                echo "  Fix the image pins in ${ENV_FILE} (or manifest.json) to release digests/tags and re-run." >&2
                exit 3
                ;;
        esac
        if [ -n "$EXPECTED" ] && [ "$IMG" != "$EXPECTED" ]; then
            echo -e "${RED}Image verification failed: compose resolved '${IMG}' but the release pin (manifest.json / ${ENV_FILE}) is '${EXPECTED}'.${NC}" >&2
            exit 3
        fi
    done <<< "$RENDERED"
    echo -e "${GREEN}Image pins verified against the rendered compose model.${NC}"
}
assert_rendered_image

# ── pull ─────────────────────────────────────────────────────────────────────
if [ "$NO_PULL" -eq 0 ]; then
    echo -e "${GREEN}Pulling the pinned images...${NC}"
    if ! run_compose pull; then
        echo -e "${RED}Image pull failed (offline host? registry auth?).${NC}" >&2
        echo "  The previous containers keep running. Re-run with --no-pull to reuse" >&2
        echo "  local images, or fix the network/credentials." >&2
        exit 2
    fi
fi

# One-time ADR-0022 DB/role rename (neuro_* → neuronection_*), before the
# stack boots against the new names. No-op on fresh installs and re-runs.
migrate_legacy_db_names

echo -e "${GREEN}Recreating the standalone stack...${NC}"
if ! run_compose up -d; then
    echo -e "${RED}docker compose up -d failed (exit above).${NC}" >&2
    report_stack_failure
    echo -e "${YELLOW}Rollback: bash scripts/update.sh --image backend=<previous pin> --image frontend=<previous pin>${NC}" >&2
    exit 3
fi

if [ "$NO_WAIT" -eq 0 ]; then
    wait_for_backend_healthy || exit 3
    wait_for_ready "$READY_URL" "$READY_HOST" || exit 3
fi

echo
echo -e "${GREEN}Update complete.${NC}"
[ -n "$MANIFEST_VERSION" ] && echo "  Version:  ${MANIFEST_VERSION}"
echo "  URL:      http://$(hostname 2>/dev/null || echo localhost):${HTTP_PORT}"
echo "  Health:   ${HEALTH_URL}"
[ -n "$READY_PATH" ] && echo "  Ready:    ${READY_URL}"
echo "  Rollback: the previous pins are kept in ${ENV_FILE}; data via scripts/restore.sh"
