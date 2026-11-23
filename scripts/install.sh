#!/bin/bash

# Health Assistant — bundle installer (the family installer contract)
#
# Runs INSIDE the extracted source-free release bundle (the directory holding
# docker/ and scripts/; the generated bootstrap puts manifest.json beside
# them) and takes a fresh host to a running standalone stack:
#   pre-flight (no root, Docker/Compose v2) → .env (generate missing secrets,
#   0600, umask 077; keep an existing file verbatim) → pin the images from
#   the release manifest / flags → port pre-flight → pull → up -d → wait for
#   /health and /ready → summary with the setup URL (printed once) and the
#   backup/update/rollback hints.
#
# Usage (from the extracted bundle root, or a source checkout root):
#   bash scripts/install.sh                            # install + start (port 8090 — the family default; 8091=study, 8092=career)
#   bash scripts/install.sh --http-port 8090            # publish the app on 8090
#   bash scripts/install.sh --image backend=ghcr.io/…@sha256:… \
#                           --image frontend=ghcr.io/…@sha256:…
#                                                      # pin-passing form (what the
#                                                      # bootstrap forwards from the
#                                                      # release manifest)
#   bash scripts/install.sh --image-tag 0.10.3          # pin a published release tag
#   bash scripts/install.sh --env-only                 # write .env only, don't start
#   bash scripts/install.sh --no-pull                  # reuse local images (offline)
#   bash scripts/install.sh --no-wait                  # skip the health/readiness wait
#   bash scripts/install.sh --auto-ports               # move busy ports to free ones
#   bash scripts/install.sh --no-ports                 # skip the port pre-flight
#   bash scripts/install.sh --app-url http://box.example.com:8090   # the URL users open (APP_URL): CORS,
#                                                         #   OAuth redirects, the setup link — and the
#                                                         #   Host header the app accepts
#   bash scripts/install.sh --bind 127.0.0.1            # publish on loopback only (behind your own proxy)
#   bash scripts/install.sh --no-token                 # never print the setup token
#   bash scripts/install.sh --reset --yes              # wipe stack volumes, reinstall
#   bash scripts/install.sh --reset --yes --reset-all  # also wipe user uploads
#   bash scripts/install.sh -h|--help                  # this help
#
# Image pins (the contract's hand-off order, per image slot — health has
# backend + frontend): --image KEY=REF > --image-tag TAG > the manifest.json
# shipped beside this bundle > the pins already in .env. A bare REF (no KEY=)
# is refused: health has two image slots, so a pin must name its slot.
# This is a source-free bundle: without a pin there is nothing to build, so
# the installer refuses. In a full source checkout scripts/run-docker.sh is
# the build path.
#
# Setup token (D12): printed ONCE in the summary, suppressed with --no-token.
# SETUP_TOKEN_MODE=env keeps it in .env (0600) — the one permitted storage,
# so the one-click /setup?token=… URL works. It is never written to an
# application log, and this script never tells you to grep one.
#
# Idempotent: a re-run keeps .env (secrets are never overwritten and never
# printed), refreshes images and re-runs `up -d`. Data volumes are never
# touched except by the explicit --reset.
#
# Exit codes (the installer contract): 0 ok · 1 usage/prerequisite ·
# 2 image pull (download) · 3 compose config/start or health-wait ·
# 4 port conflict.
#
# Windows: requires WSL2 or Git-Bash (bash).

print_help() {
  sed -n '3,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit 0
}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib-docker.sh"

# ── flags ───────────────────────────────────────────────────────────────────
ENV_ONLY=0 NO_PULL=0 NO_WAIT=0 AUTO_PORTS=0 NO_PORTS=0 RESET=0 RESET_YES=0 RESET_ALL=0
NO_TOKEN=0 IMAGE_TAG_ARG="" HTTP_PORT_ARG="" APP_URL_FLAG="" BIND_ARG="" ALLOW_ROOT="${ALLOW_ROOT:-}"
IMAGE_ARGS=()   # --image values (KEY=REF), resolved after the config load
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) print_help ;;
        --env-only) ENV_ONLY=1 ;;
        --no-pull) NO_PULL=1 ;;
        --no-wait) NO_WAIT=1 ;;
        --auto-ports) AUTO_PORTS=1 ;;
        --no-ports) NO_PORTS=1 ;;
        --reset) RESET=1 ;;
        --yes) RESET_YES=1 ;;
        --reset-all) RESET_ALL=1 ;;
        --no-token) NO_TOKEN=1 ;;
        --allow-root) ALLOW_ROOT=1 ;;
        --image)
            [ "$#" -ge 2 ] || die "--image needs a value (KEY=REF)"
            IMAGE_ARGS+=("$2"); shift ;;
        --image-tag)
            [ "$#" -ge 2 ] || die "--image-tag needs a value"
            IMAGE_TAG_ARG="$2"; shift ;;
        --app-url)
            [ "$#" -ge 2 ] || die "--app-url needs a value"
            APP_URL_FLAG="$2"; shift ;;
        --bind)
            [ "$#" -ge 2 ] || die "--bind needs an IP address"
            BIND_ARG="$2"; shift ;;
        --http-port)
            [ "$#" -ge 2 ] || die "--http-port needs a value"
            case "$2" in ''|*[!0-9]*) die "--http-port needs a numeric PORT (got: $2)" ;; esac
            HTTP_PORT_ARG="$2"; shift ;;
        *)
            echo -e "${RED}Unknown parameter: $1 (try --help)${NC}" >&2
            exit 1
            ;;
    esac
    shift
done

# ── pre-flight ──────────────────────────────────────────────────────────────
# No root, no sudo (the installer contract): the stack runs entirely through
# Docker as an unprivileged user. --allow-root exists for CI and disposable
# smoke containers only.
if [ "$(id -u)" = "0" ] && [ -z "$ALLOW_ROOT" ]; then
    echo -e "${RED}Refusing to run as root (the installer contract: no root, no sudo).${NC}" >&2
    echo "  Re-run as your normal account (add it to the docker group if needed:" >&2
    echo "  sudo usermod -aG docker <user>, then log in again)." >&2
    echo "  CI/disposable containers only: --allow-root" >&2
    exit 1
fi
[ -n "$ALLOW_ROOT" ] && echo -e "${YELLOW}--allow-root: continuing as root (CI/disposable use only).${NC}"

if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    echo "bash >= 4 is required (found ${BASH_VERSION:-unknown})." >&2
    exit 1
fi

check_cwd
check_docker
# The standalone stack relies on compose-spec features (service_completed_
# successfully, profiles) — the v1 fallback lib-docker.sh tolerates elsewhere
# is not enough here.
[ "$DOCKER_COMPOSE_CMD" = "docker compose" ] \
    || die "Docker Compose v2 ('docker compose') is required; legacy docker-compose v1 cannot run the standalone stack."

# .env generation, compose-config parsing and the JSON helpers all need it —
# the same requirement the previous setup_env.py flow had, stated up front.
command -v python3 &>/dev/null \
    || die "python3 is required (env generation, compose model parsing). Install Python 3, or copy .env.example to .env and fill it in manually."

INSTALL_DIR="$(pwd)"

# ── product config (installer.json ships with the bundle) ───────────────────
# Only installer.json's schema fields are read; everything product-specific is
# derived, never hardcoded twice.
HEALTH_PATH="$(json_field installer.json healthPath)"; [ -n "$HEALTH_PATH" ] || HEALTH_PATH="/health"
# health ships /ready (readiness: migrate finished, DB at head).
READY_PATH="$(json_field installer.json readyPath)"
DOCS_URL="$(json_field installer.json docsUrl)"; [ -n "$DOCS_URL" ] || DOCS_URL="https://health-assistant.io/docs"
IMAGE_ENV="$(json_field installer.json imageEnv)"; [ -n "$IMAGE_ENV" ] || IMAGE_ENV="IMAGE_TAG"
IMAGE_KEYS="$(json_keys installer.json imageKeys)"
if [ -z "$IMAGE_KEYS" ]; then
    echo -e "${YELLOW}Could not read imageKeys from installer.json — accepting 'backend frontend'.${NC}"
    IMAGE_KEYS="backend frontend"
fi
DEFAULT_PORT="$(json_field installer.json defaultPort)"
case "$DEFAULT_PORT" in ''|*[!0-9]*) DEFAULT_PORT=8090 ;; esac
MIN_RAM_GB="$(json_field installer.json minRamGb)"
case "$MIN_RAM_GB" in ''|*[!0-9]*) MIN_RAM_GB=8 ;; esac
# The setup hint derives from setupMode (the schema's closed vocabulary).
SETUP_MODE="$(json_field installer.json setupMode)"
[ "$SETUP_MODE" = "setup token" ] \
    || echo -e "${YELLOW}Unexpected setupMode '${SETUP_MODE:-?}' in installer.json — continuing with the token flow.${NC}"
MANIFEST_VERSION="$(json_field manifest.json version)"

# RAM/disk hints — never a hard fail (containers may not expose meminfo).
if [ -r /proc/meminfo ]; then
    MEM_MB="$(awk '/^MemTotal:/ {print int($2/1024); exit}' /proc/meminfo 2>/dev/null || true)"
    if [ -n "$MEM_MB" ] && [ "$MEM_MB" -lt "$((MIN_RAM_GB * 1024))" ]; then
        echo -e "${YELLOW}RAM hint: ${MIN_RAM_GB} GB is the family minimum; this host reports ${MEM_MB} MB.${NC}"
    fi
fi
if command -v df >/dev/null 2>&1; then
    AVAIL_MB="$(df -Pm . 2>/dev/null | awk 'NR==2 {print $4}' || true)"
    if [ -n "$AVAIL_MB" ] && [ "$AVAIL_MB" -gt 0 ] && [ "$AVAIL_MB" -lt 5120 ]; then
        echo -e "${YELLOW}Disk hint: ${AVAIL_MB} MB free — image pulls and uploads need headroom.${NC}"
    fi
fi

# ── image pins (pinned releases only — never latest) ────────────────────────
# Health declares two image slots (installer.json imageKeys): backend and
# frontend. The compose files consume them as HA_BACKEND_IMAGE /
# HA_FRONTEND_IMAGE (immutable digest overrides) with IMAGE_TAG as the
# tag-only fallback path — the pin writer below speaks both.

# The manifest's pin for one slot (manifest.json is placed beside the bundle
# by the bootstrap; a source checkout has no manifest, only .env/flags).
manifest_pin() { json_field manifest.json "images.$1"; }

# The repository part of an image ref (strip @sha256:… or :tag) — derived
# from the manifest/existing pin, never a second hardcoded constant.
image_repo_hint() {
    case "$1" in
        *@sha256:*) printf '%s' "${1%%@*}" ;;
        *:*)       printf '%s' "${1%%:*}" ;;
        *)         printf '%s' "$1" ;;
    esac
}

# The compose files' own default repo expression (REGISTRY/REPOSITORY with
# their .env defaults) — used ONLY to reconstruct a tag pin for a legacy
# IMAGE_TAG-only .env, and validated afterwards against the rendered model.
default_repo() { # slot
    local REG REPO
    REG="$(env_value REGISTRY "ghcr.io")"
    REPO="$(env_value REPOSITORY "health-assistant-io/health-assistant")"
    printf '%s/%s/health-assistant-%s' "$REG" "$REPO" "$1"
}

BACKEND_PIN="" FRONTEND_PIN=""
OPERATOR_TYPED=0

# 1. --image KEY=REF — the bootstrap forwards one per manifest image slot.
#    A bare REF is refused: with two slots a pin must name its slot (a bare
#    ref cannot mean both — the images are different repositories).
for ARG in ${IMAGE_ARGS[@]+"${IMAGE_ARGS[@]}"}; do
    KEY="" REF="$ARG"
    # Image refs never contain '=': a value that does is the bootstrap's
    # KEY=REF pin-passing form (the hand-off form the one-liner uses).
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
        backend)  BACKEND_PIN="$REF" ;;
        frontend) FRONTEND_PIN="$REF" ;;
    esac
    OPERATOR_TYPED=1
done

# 2. --image-tag X.Y.Z → <repo>:X.Y.Z per slot, the repo derived from the
#    manifest (or the existing pin) — installer.json carries no repo constant.
if [ -z "$BACKEND_PIN$FRONTEND_PIN" ] && [ -n "$IMAGE_TAG_ARG" ]; then
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
            BACKEND_PIN="$(image_repo_hint "$REPO_SRC"):${TAG}"
        else
            FRONTEND_PIN="$(image_repo_hint "$REPO_SRC"):${TAG}"
        fi
    done
    OPERATOR_TYPED=1
fi

# 3. the release manifest next to this bundle (the normal bundle install).
[ -z "$BACKEND_PIN" ]  && BACKEND_PIN="$(manifest_pin backend)"
[ -z "$FRONTEND_PIN" ] && FRONTEND_PIN="$(manifest_pin frontend)"

# 4. the pins already in .env (re-run without a manifest): the compose
#    immutable overrides first, else a legacy tag pin reconstructed from
#    IMAGE_TAG via the compose files' own default repo parts.
if [ -z "$BACKEND_PIN" ] && [ -f "$ENV_FILE" ]; then
    BACKEND_PIN="$(env_value HA_BACKEND_IMAGE '')"
    if [ -z "$BACKEND_PIN" ]; then
        LEGACY_TAG="$(env_value "$IMAGE_ENV" '')"
        [ -n "$LEGACY_TAG" ] && BACKEND_PIN="$(default_repo backend):$(image_repo_hint "x:${LEGACY_TAG}")"
    fi
fi
if [ -z "$FRONTEND_PIN" ] && [ -f "$ENV_FILE" ]; then
    FRONTEND_PIN="$(env_value HA_FRONTEND_IMAGE '')"
    if [ -z "$FRONTEND_PIN" ]; then
        LEGACY_TAG="$(env_value "$IMAGE_ENV" '')"
        [ -n "$LEGACY_TAG" ] && FRONTEND_PIN="$(default_repo frontend):$(image_repo_hint "x:${LEGACY_TAG}")"
    fi
fi

# Operator-typed mutable pins are refused here, before anything is written;
# manifest/env-file-sourced pins hit the same wall later, against the
# RENDERED compose model (exit 3 — a compose-config failure, before anything
# is pulled or started).
if [ "$OPERATOR_TYPED" -eq 1 ]; then
    for PIN in "$BACKEND_PIN" "$FRONTEND_PIN"; do
        [ -n "$PIN" ] || continue
        case "$PIN" in
            *:latest|*:main)
                die "Refusing the mutable pin '$PIN' — latest/main float with the develop branch and can break the migrate step. The installer contract pins releases only; pass a release tag or a digest."
                ;;
        esac
    done
fi

if [ -n "$BACKEND_PIN" ] && [ -n "$FRONTEND_PIN" ]; then
    :
elif [ "$ENV_ONLY" -eq 1 ]; then
    echo -e "${YELLOW}Warning: no complete image pin found — .env will be written without one.${NC}"
    echo "  Set them before starting: --image backend=<ref> --image frontend=<ref>,"
    echo "  --image-tag X.Y.Z, or place the release manifest.json next to this bundle and re-run."
else
    echo -e "${RED}No image pin. This is a source-free bundle — there is nothing to build.${NC}" >&2
    echo "  Pick one:" >&2
    echo "    * recommended: place manifest.json (from the same GitHub release) next to" >&2
    echo "      this bundle — the installer pins the exact image digests from it;" >&2
    echo "    * or re-run with --image-tag X.Y.Z (a published release tag);" >&2
    echo "    * or pass --image backend=<ref> --image frontend=<ref> explicitly." >&2
    echo "  In a full source checkout, scripts/run-docker.sh is the build path." >&2
    exit 1
fi

# Release-skew guard: a hand-passed --image-tag must agree with the bundle's
# own manifest (the tree and the images must describe the same contract).
if [ -n "$IMAGE_TAG_ARG" ] && [ -n "$MANIFEST_VERSION" ]; then
    MV="${MANIFEST_VERSION#v}"
    if [ "$MV" != "${IMAGE_TAG_ARG#v}" ]; then
        die "--image-tag ${IMAGE_TAG_ARG} does not match this bundle's manifest (version ${MV}). Pin the matching release, or drop --image-tag and let the manifest pin it."
    fi
fi

# ── host port ────────────────────────────────────────────────────────────────
HTTP_PORT="${HTTP_PORT_ARG:-${HEALTH_HTTP_PORT:-$(env_value HTTP_PORT '')}}"
[ -n "$HTTP_PORT" ] || HTTP_PORT="$DEFAULT_PORT"
# ── bind address (optional; default 0.0.0.0, loopback behind your proxy) ───
HTTP_BIND="${BIND_ARG:-${HEALTH_HTTP_BIND:-$(env_value HTTP_BIND '')}}"
[ -n "$HTTP_BIND" ] || HTTP_BIND="0.0.0.0"
[ "$HTTP_BIND" = "localhost" ] && HTTP_BIND="127.0.0.1"   # compose needs an IP literal
case "$HTTP_BIND" in
    *[!0-9.]*) die "--bind must be an IP address like 127.0.0.1 (got: $HTTP_BIND)" ;;
esac
IFS='.' read -r B1 B2 B3 B4 <<BIND_EOF
$HTTP_BIND
BIND_EOF
for B in "$B1" "$B2" "$B3" "$B4"; do
    case "$B" in ''|*[!0-9]*) die "--bind must be an IPv4 address like 127.0.0.1 (got: $HTTP_BIND)" ;; esac
    [ "$B" -le 255 ] || die "--bind octet out of range in ${HTTP_BIND}"
done

case "$HTTP_PORT" in ''|*[!0-9]*) die "HTTP_PORT must be numeric (got: $HTTP_PORT)" ;; esac
HEALTH_URL="http://127.0.0.1:${HTTP_PORT}${HEALTH_PATH}"
READY_URL="http://127.0.0.1:${HTTP_PORT}${READY_PATH}"

# ── .env (never overwrite an existing one) ────────────────────────────────────
# The bundle ships .env.example; the installer fills it in. Everything is
# written under umask 077 and chmod 600: .env holds the key family, the DB
# credentials, Redis/Flower secrets and the setup token.
gen_secret() { # [BYTES] → urlsafe random token (32 bytes default)
    local BYTES="${1:-32}"
    python3 -c 'import secrets, sys; print(secrets.token_urlsafe(int(sys.argv[1])))' "$BYTES"
}

# A Fernet key (the HA_DATA_KEY shape): 32 random bytes, urlsafe base64 with
# padding — exactly what cryptography.fernet.Fernet.generate_key() produces,
# from the stdlib alone.
gen_fernet_key() {
    python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
}

# A VAPID P-256 key pair for Web Push (required in production — the backend
# refuses to boot without it). openssl generates the key; python3 (stdlib
# only) extracts the raw scalar and the uncompressed point, both base64url
# unpadded — the shape pywebpush and the browser expect. No `cryptography`
# package needed on the host.
gen_vapid_keys() { # prints "PUBLIC\nPRIVATE"
    local PEM
    PEM="$(mktemp)"
    openssl ecparam -name prime256v1 -genkey -noout -out "$PEM" 2>/dev/null || { rm -f "$PEM"; return 1; }
    openssl ec -in "$PEM" -outform DER 2>/dev/null | python3 -c '
import base64, sys
der = sys.stdin.buffer.read()
# ECPrivateKey DER (P-256): 02 01 01 | 04 20 <32-byte private scalar> |
# [0] namedCurve | [1] BIT STRING <65-byte uncompressed point (0x04||X||Y)>.
if len(der) < 100 or der[5] != 0x04 or der[6] != 0x20 or der[-65] != 0x04:
    sys.exit(1)
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")
print(b64(der[-65:]))
print(b64(der[7:39]))
' || { rm -f "$PEM"; return 1; }
    rm -f "$PEM"
}

ENV_WAS_FRESH=0
if [ -f "$ENV_FILE" ]; then
    echo -e "${GREEN}${ENV_FILE} already exists — leaving it untouched (secrets, pins and ports are kept).${NC}"
else
    # The template ships as docker/.env.example in the release bundle and
    # .env.example at a source-checkout root — accept both.
    ENV_TEMPLATE=".env.example"
    [ -f "$ENV_TEMPLATE" ] || ENV_TEMPLATE="docker/.env.example"
    [ -f "$ENV_TEMPLATE" ] \
        || die "no .env.example — this is not a complete bundle (or checkout)."
    echo -e "${GREEN}No ${ENV_FILE} found — creating one from the example and generating fresh secrets.${NC}"
    # One question, like the old Quick Start: the public app URL (drives CORS,
    # OAuth redirects, the setup URL and Host validation). The default is the
    # machine's hostname at the chosen port — for TTY *and* piped installs
    # (curl | bash never sees a TTY, and a silent localhost default 400s from
    # other machines). --app-url (or APP_URL in the env) skips the question.
    APP_URL_DEFAULT="http://$(hostname 2>/dev/null || echo localhost):${HTTP_PORT}"
    if [ -n "$APP_URL_FLAG" ]; then
        APP_URL="$APP_URL_FLAG"
        case "$APP_URL" in
            *://*) ;;
            *) APP_URL="http://${APP_URL}" ;;
        esac
        APP_URL="${APP_URL%/}"
    else
        APP_URL="$APP_URL_DEFAULT"
        if [ -t 0 ]; then
            read -r -p "Public App URL [${APP_URL_DEFAULT}]: " APP_URL || APP_URL=""
            APP_URL="$(printf '%s' "$APP_URL" | sed 's/[[:space:]]*$//')"
            [ -n "$APP_URL" ] || APP_URL="$APP_URL_DEFAULT"
            case "$APP_URL" in
                *://*) ;;
                *) APP_URL="http://${APP_URL}" ;;
            esac
            APP_URL="${APP_URL%/}"
        else
            echo -e "${YELLOW}No --app-url given — assuming ${APP_URL_DEFAULT} (this machine's hostname).${NC}"
            echo -e "${YELLOW}If you open the app by another name/IP, rerun with: --app-url http://<that-host>:${HTTP_PORT}${NC}"
        fi
    fi
    ( umask 077; cp "$ENV_TEMPLATE" "$ENV_FILE" )
    chmod 600 "$ENV_FILE"
    set_env_value APP_URL "$APP_URL"
    # Production-oriented defaults (the old Quick Start posture).
    set_env_value HA_APP_ENV "production"
    set_env_value DEBUG "false"
    set_env_value HA_TRUSTED_PROXY_COUNT "1"
    set_env_value CELERY_WORKER_CONCURRENCY "2"
    set_env_value HA_DEMO_MODE "false"
    # The §8 per-purpose key family + ADR-0064 credential split: every value
    # independent (owner/app distinct — init-roles.sh refuses a shared one).
    set_env_value HA_SESSION_KEY "$(gen_secret 48)"
    set_env_value HA_REFRESH_KEY "$(gen_secret 48)"
    set_env_value HA_DATA_KEY "$(gen_fernet_key)"
    OWNER_PW="$(gen_secret 24)"
    APP_PW="$(gen_secret 24)"
    while [ "$OWNER_PW" = "$APP_PW" ]; do APP_PW="$(gen_secret 24)"; done
    set_env_value POSTGRES_PASSWORD "$OWNER_PW"
    set_env_value HA_DB_PASSWORD "$APP_PW"
    set_env_value REDIS_PASSWORD "$(gen_secret 24)"
    set_env_value FLOWER_PASSWORD "$(gen_secret 24)"
    # Web Push VAPID pair (prod-guard: the app refuses to boot without it).
    VAPID_EMAIL="admin@example.com"
    case "$APP_URL" in
        http://localhost*|http://127.0.0.1*) ;;
        *) VAPID_EMAIL="admin@$(printf '%s' "$APP_URL" | sed -E 's#^[a-z]+://([^:/]+).*#\1#')";;
    esac
    if VAPID_PAIR="$(gen_vapid_keys)"; then
        set_env_value VAPID_PUBLIC_KEY "$(printf '%s\n' "$VAPID_PAIR" | sed -n 1p)"
        set_env_value VAPID_PRIVATE_KEY "$(printf '%s\n' "$VAPID_PAIR" | sed -n 2p)"
        set_env_value VAPID_ADMIN_EMAIL "$VAPID_EMAIL"
    else
        echo -e "${YELLOW}Could not generate the VAPID Web Push keys (openssl?). The app refuses to boot in production without them — generate a pair and set VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY in ${ENV_FILE} (see docs/INSTALL.md).${NC}"
    fi
    # Setup token (D12): env mode with a generated bootstrap token. .env at
    # 0600 is the one permitted storage; the summary prints the one-click URL
    # once (unless --no-token); the token never reaches an application log.
    set_env_value SETUP_TOKEN_MODE "env"
    set_env_value SETUP_BOOTSTRAP_TOKEN "$(gen_secret 24)"
    # A production .env carries no demo credentials (they stay in the example
    # for the screenshot tooling) — same posture as the old Quick Start.
    sed -i.bak -E 's/^(HA_DEMO_EMAIL=.*)$/# \1/; s/^(HA_DEMO_PASSWORD=.*)$/# \1/' "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    echo -e "${GREEN}Generated fresh per-instance secrets (values not printed).${NC}"
    ENV_WAS_FRESH=1
fi

# ── persist the resolved pins (only when they change) ───────────────────────
# Pins — digest or tag — live in the compose files' immutable override vars
# (HA_BACKEND_IMAGE / HA_FRONTEND_IMAGE), exactly what the renderer consumes.
# IMAGE_TAG additionally records the release tag for the operator (readable,
# and the legacy seam pre-contract installs pinned with). A re-run of the same
# release is a byte-for-byte no-op on .env.
write_pins() {
    local SLOT VAR PIN CUR TAGPART
    for SLOT in $IMAGE_KEYS; do
        VAR="HA_$(echo "$SLOT" | tr '[:lower:]' '[:upper:]')_IMAGE"
        PIN=""
        [ "$SLOT" = "backend" ] && PIN="$BACKEND_PIN"
        [ "$SLOT" = "frontend" ] && PIN="$FRONTEND_PIN"
        if [ -n "$PIN" ]; then
            CUR="$(env_value "$VAR" '')"
            if [ "$PIN" != "$CUR" ]; then
                set_env_value "$VAR" "$PIN"
                echo -e "${GREEN}Pinned ${VAR}=${PIN} (saved in ${ENV_FILE}).${NC}"
            fi
        fi
    done
    TAGPART=""
    if [ -n "$IMAGE_TAG_ARG" ]; then
        TAGPART="${IMAGE_TAG_ARG#v}"
    else
        # the release tag carried by a digest pin (name:tag@digest)
        case "$BACKEND_PIN" in
            *@sha256:*) TAGPART="${BACKEND_PIN%%@*}"; TAGPART="${TAGPART##*:}" ;;
        esac
    fi
    if [ -n "$TAGPART" ] && [ "$TAGPART" != "$(env_value "$IMAGE_ENV" '')" ]; then
        set_env_value "$IMAGE_ENV" "$TAGPART"
    fi
}
write_pins

# Update-path guard: an install made before the 8090 default must not
# silently move ports on a re-run. No persisted HTTP_PORT + no explicit
# input → keep the historical 80, loudly.
if [ "$ENV_WAS_FRESH" -eq 0 ] && [ -z "$HTTP_PORT_ARG" ] && [ -z "${HEALTH_HTTP_PORT:-}" ] \
   && [ -z "$(env_value HTTP_PORT '')" ] && [ "$HTTP_PORT" != "80" ]; then
    echo -e "${YELLOW}This install predates persisted ports: keeping HTTP_PORT=80 (the old default) — set --http-port 8090 to move it.${NC}"
    HTTP_PORT=80
fi
if [ "$HTTP_PORT" != "$(env_value HTTP_PORT '')" ]; then
    set_env_value HTTP_PORT "$HTTP_PORT"
    echo -e "${GREEN}Set HTTP_PORT=${HTTP_PORT} (saved in ${ENV_FILE}).${NC}"
fi
# Persist the bind only when explicitly chosen: a re-run with no input
# must stay a byte-for-byte no-op on the env file.
if [ -n "$BIND_ARG" ] || [ -n "${HEALTH_HTTP_BIND:-}" ]; then
    if [ "$HTTP_BIND" != "$(env_value HTTP_BIND '')" ]; then
        set_env_value HTTP_BIND "$HTTP_BIND"
        echo -e "${GREEN}Set HTTP_BIND=${HTTP_BIND} (saved in ${ENV_FILE}).${NC}"
    fi
fi
# The port may have moved in the pre-flight below; keep the URLs honest.
HTTP_PORT="$(env_value HTTP_PORT "$HTTP_PORT")"
HEALTH_URL="http://127.0.0.1:${HTTP_PORT}${HEALTH_PATH}"
READY_URL="http://127.0.0.1:${HTTP_PORT}${READY_PATH}"

# A fresh .env cannot log into a database volume left by a previous install
# (postgres only applies POSTGRES_PASSWORD on first init) — offer the reset
# or the restore path before the migrate job fails with a confusing auth
# error. Refusing here protects the previous install's data.
check_leftover_db_volume "$ENV_WAS_FRESH"

# ── --env-only: stop after the environment is ready ─────────────────────────
if [ "$ENV_ONLY" -eq 1 ]; then
    echo
    echo -e "${GREEN}Done — ${ENV_FILE} is ready. Start the stack with:${NC}"
    echo "  bash scripts/install.sh"
    echo "  (then: ${HEALTH_URL})"
    exit 0
fi

# ── --reset: deliberate, destructive, confirmed ─────────────────────────────
if [ "$RESET" -eq 1 ]; then
    RESET_ARGS=""
    if [ "$RESET_YES" -eq 1 ]; then RESET_ARGS="$RESET_ARGS --yes"; fi
    if [ "$RESET_ALL" -eq 1 ]; then RESET_ARGS="$RESET_ARGS --all"; fi
    # shellcheck disable=SC2086
    reset_stack_data $RESET_ARGS
fi

# ── port pre-flight (D10: before anything is pulled or started) ──────────────
if [ "$NO_PORTS" -eq 0 ]; then
    if [ "$AUTO_PORTS" -eq 1 ]; then
        check_published_ports --auto
    else
        check_published_ports
    fi
    # --auto-ports may have moved the port in .env.
    HTTP_PORT="$(env_value HTTP_PORT "$HTTP_PORT")"
    HEALTH_URL="http://127.0.0.1:${HTTP_PORT}${HEALTH_PATH}"
    READY_URL="http://127.0.0.1:${HTTP_PORT}${READY_PATH}"
fi

# ── image verification (the installer asserts the resolved images) ───────────
# The rendered compose model is the single wall for manifest/env-file-sourced
# pins: rendered image(s) must match the release pins and must never be a
# mutable ref. Exit 3 (compose config failure); operator-typed mutable refs
# were already refused at pin resolution, so this catches manifest drift,
# stale .env pins and tampered manifests.
assert_rendered_image() {
    local RENDERED SLOT EXPECTED IMG
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
    [ -n "$RENDERED" ] || { echo -e "${YELLOW}Skipping the image assert (compose model not renderable).${NC}"; return 0; }
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
            echo "  ${ENV_FILE} and docker/docker-compose.standalone.yml disagree about the image." >&2
            exit 3
        fi
    done <<< "$RENDERED"
    [ -n "$BACKEND_PIN" ]  && echo -e "${GREEN}Image pin verified (backend):  ${BACKEND_PIN}${NC}"
    [ -n "$FRONTEND_PIN" ] && echo -e "${GREEN}Image pin verified (frontend): ${FRONTEND_PIN}${NC}"
}
assert_rendered_image

# ── upgrade pre-flight: refuse a pre-0.9 database / stale .env early ─────────
run_upgrade_preflight

# Adopt volumes from before the compose files pinned their project name
# (docker_* → <project>_*), so the reset/guard/down below reason about the
# current volume names instead of leaving real data behind a stale prefix.
adopt_legacy_volumes

# ── pull ─────────────────────────────────────────────────────────────────────
if [ "$NO_PULL" -eq 0 ]; then
    echo -e "${GREEN}Pulling the pinned images...${NC}"
    if ! run_compose pull; then
        echo -e "${RED}Image pull failed (offline host? registry auth?).${NC}" >&2
        echo "  Re-run with --no-pull to reuse local images, or fix the network/credentials." >&2
        exit 2
    fi
else
    for PIN in "$BACKEND_PIN" "$FRONTEND_PIN"; do
        [ -n "$PIN" ] || continue
        docker image inspect "$PIN" >/dev/null 2>&1 \
            || echo -e "${YELLOW}--no-pull: ${PIN} is not present locally — 'up' cannot fall back to building (a bundle has no source). Drop --no-pull to pull it first.${NC}"
    done
fi

# One-time ADR-0022 DB/role rename (neuro_* → neuronection_*), before the
# stack boots against the new names. No-op on fresh installs.
migrate_legacy_db_names

echo -e "${GREEN}Starting the standalone stack...${NC}"
if ! run_compose up -d; then
    echo -e "${RED}docker compose up -d failed (exit above).${NC}" >&2
    report_stack_failure
    exit 3
fi

# ── health + readiness wait ──────────────────────────────────────────────────
if [ "$NO_WAIT" -eq 0 ]; then
    wait_for_backend_healthy || exit 3
    wait_for_ready "$READY_URL" || exit 3
fi

# ── summary (URLs, setup once, backup, update, rollback) ────────────────────
short_pin() {
    case "$1" in
        *@sha256:*) printf '%s' "${1%%@*}@${1##*@}" ;;
        *) printf '%s' "$1" ;;
    esac
}
HOST_HINT="$(hostname 2>/dev/null || echo localhost)"

echo
echo -e "${GREEN}======================================================${NC}"
echo -e "${GREEN}   Health Assistant is running!${NC}"
echo -e "${GREEN}======================================================${NC}"
echo "  Install:  ${INSTALL_DIR}"
[ -n "$MANIFEST_VERSION" ] && echo "  Version:  ${MANIFEST_VERSION}"
[ -n "$BACKEND_PIN" ]  && echo "  Backend:  $(short_pin "$BACKEND_PIN")"
[ -n "$FRONTEND_PIN" ] && echo "  Frontend: $(short_pin "$FRONTEND_PIN")"
APP_URL_SUMMARY="$(env_value APP_URL '')"
if [ -n "$APP_URL_SUMMARY" ]; then
    echo "  URL:      ${APP_URL_SUMMARY}   (APP_URL — the address Host validation and cookies accept)"
else
    echo "  URL:      http://${HOST_HINT}:${HTTP_PORT}   (or http://<your-host>:${HTTP_PORT})"
fi
echo "  Bind:     ${HTTP_BIND}:${HTTP_PORT} → container :80"
echo "  Health:   ${HEALTH_URL}"
[ -n "$READY_PATH" ] && echo "  Ready:    ${READY_URL}"
echo "  Docs:     ${DOCS_URL}"

# Setup token (D12): printed ONCE, here — never by a second printer, never to
# an application log. SETUP_TOKEN_MODE=env keeps it in .env (0600, the one
# permitted storage); --no-token suppresses the value and points at .env.
SETUP_URL_BASE="$(env_value APP_URL '')"
[ -z "$SETUP_URL_BASE" ] && SETUP_URL_BASE="http://${HOST_HINT}:${HTTP_PORT}"
BOOTSTRAP_TOKEN="$(env_value SETUP_BOOTSTRAP_TOKEN '')"
if [ -n "$BOOTSTRAP_TOKEN" ] && [ "$NO_TOKEN" -eq 0 ]; then
    echo "  Setup:    ${SETUP_URL_BASE}/setup?token=${BOOTSTRAP_TOKEN}"
    echo "            (one-click first-run setup — create your admin account;"
    echo "             also stored in ${ENV_FILE} as SETUP_BOOTSTRAP_TOKEN)"
elif [ -n "$BOOTSTRAP_TOKEN" ]; then
    echo "  Setup:    open the app URL and use the first-run setup wizard"
    echo "            (--no-token: the one-click token is in ${ENV_FILE}, see SETUP_BOOTSTRAP_TOKEN)"
else
    echo "  Setup:    open the app URL and use the first-run setup wizard"
fi
if ! grep -qE '^OPENAI_API_KEY=.+' "$ENV_FILE"; then
    echo
    echo "  Note:     no OPENAI_API_KEY set — AI features (OCR, extraction, chat)"
    echo "            need an AI provider key configured in-app (System Admin → AI)"
    echo "            or via the OPENAI_* env vars. The app runs fine without one."
fi
echo
echo -e "${YELLOW}Next steps:${NC}"
echo "  * Backups: bash scripts/backup.sh (set BACKUP_DESTINATION in ${ENV_FILE} for off-host copies — T1)"
echo "  * Updates: extract the new release bundle over ${INSTALL_DIR}, then bash scripts/update.sh"
echo "  * Rollback: the previous pin is kept by update.sh; data restores via scripts/restore.sh"
