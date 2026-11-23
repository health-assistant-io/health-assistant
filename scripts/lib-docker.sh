#!/bin/bash

# Health Assistant — shared helpers for the Docker ops scripts.
#
# Sourced by scripts/install.sh, scripts/update.sh,
# scripts/upgrade-check.sh, scripts/backup.sh and scripts/restore.sh;
# not meant to be run directly.
#
# Runs in BOTH supported layouts: a source checkout (backend/ + frontend/)
# and the extracted source-free release bundle (docker/ + scripts/ +
# installer.json). Nothing here may read the source tree for anything the
# installer needs — the release version and image pins come from
# manifest.json / installer.json beside the bundle scripts.

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

COMPOSE_FILE="docker/docker-compose.standalone.yml"
COMPOSE_ENV_ARGS=(--env-file .env -f "$COMPOSE_FILE")

# The stack's env file: root .env (health's family-declared location —
# career/study use docker/.env; installer.json is the source of truth).
ENV_FILE=".env"

# Directory of this library (the bundle's scripts/ dir) — used to reach
# sibling scripts (upgrade-check.sh) without relying on the caller's cwd.
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() {
    echo -e "${RED}Error: $1${NC}" >&2
    exit 1
}

# run_compose ARGS... — docker compose with the standalone stack args.
# Kept as a function so every caller shares one invocation shape.
run_compose() {
    # DOCKER_COMPOSE_CMD intentionally word-splits: "docker compose" or "docker-compose".
    # shellcheck disable=SC2086
    $DOCKER_COMPOSE_CMD "${COMPOSE_ENV_ARGS[@]}" "$@"
}

# ── layout check ─────────────────────────────────────────────────────────────
# Source checkout: the classic repo layout… or the source-free release bundle
# (health-compose-v*.tar.gz): docker/ + scripts/ + installer.json. The bundle
# marker is installer.json (the contract product config); manifest.json is
# accepted too because the bootstrap copies it beside the extracted bundle.
check_cwd() {
    if [ -d "backend" ] && [ -d "frontend" ]; then
        return 0
    fi
    if [ -d "docker" ] && { [ -f "installer.json" ] || [ -f "manifest.json" ]; }; then
        return 0
    fi
    die "Please run this script from the Health Assistant root directory (a source checkout or an extracted health-compose bundle)"
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        die "Docker is not installed. Please install Docker first."
    fi
    if ! docker info &> /dev/null; then
        die "Docker daemon is not running. Please start Docker first."
    fi
    DOCKER_COMPOSE_CMD="docker compose"
    if ! docker compose version &> /dev/null; then
        if command -v docker-compose &> /dev/null; then
            DOCKER_COMPOSE_CMD="docker-compose"
        else
            die "Docker Compose is not installed (neither 'docker compose' nor 'docker-compose' is available)."
        fi
    fi
}

require_env() {
    if [ ! -f "$ENV_FILE" ]; then
        die "'${ENV_FILE}' file not found in project root. Run './scripts/install.sh' to generate one."
    fi
}

# ── $ENV_FILE helpers (shared installer/updater helpers) ─────────────────────
# Written to survive `set -euo pipefail` in the sourcing script.

# Read one KEY from $ENV_FILE (last occurrence wins, compose's own rule).
# Usage: env_value KEY [default]
env_value() {
    local VALUE
    VALUE="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
    printf '%s' "${VALUE:-$2}"
}

# Persist KEY=VALUE in $ENV_FILE without disturbing anything else. Replaces
# every uncommented occurrence (a duplicate cannot shadow the new value),
# appends when absent, writes through a temp file so a failure cannot
# truncate $ENV_FILE (and `cat >` keeps the existing 0600 permissions).
# The caller is responsible for never clobbering a KEY the operator set by
# hand — the installer only writes pins/ports and freshly generated secrets.
# Usage: set_env_value KEY VALUE
set_env_value() {
    local KEY="$1" VALUE="$2" TMP
    [ -f "$ENV_FILE" ] || die "set_env_value: $ENV_FILE not found in $(pwd)"
    TMP="$(mktemp)"
    awk -v k="$KEY" -v v="$VALUE" '
        index($0, k "=") == 1 { print k "=" v; next }
        { print }
    ' "$ENV_FILE" > "$TMP" || { rm -f "$TMP"; die "Could not update ${KEY} in ${ENV_FILE}"; }
    if ! grep -qE "^${KEY}=" "$TMP"; then
        printf '%s=%s\n' "$KEY" "$VALUE" >> "$TMP"
    fi
    cat "$TMP" > "$ENV_FILE"
    rm -f "$TMP"
}

# ── JSON helpers (manifest.json / installer.json in the bundle) ──────────────
# One scalar field from a JSON file, dotted-path form ("images.backend").
# Strings and numbers; booleans and null read as empty. python3 when
# available, sed fallback for flat fields, empty when the file or key is
# missing — callers carry their own defaults.
# Usage: json_field FILE KEY[.KEY…]
json_field() {
    local FILE="$1" KEYPATH="$2" OUT=""
    [ -f "$FILE" ] || { printf ''; return 0; }
    if command -v python3 &>/dev/null; then
        OUT="$(python3 -c '
import json, sys
try:
    node = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for part in sys.argv[2].split("."):
    node = node.get(part) if isinstance(node, dict) else None
    if node is None:
        sys.exit(0)
if isinstance(node, (str, int, float)) and not isinstance(node, bool):
    print(node)
' "$FILE" "$KEYPATH" 2>/dev/null || true)"
    fi
    if [ -z "$OUT" ]; then
        OUT="$(sed -n "s/.*\"${KEYPATH##*.}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$FILE" 2>/dev/null | head -n1)"
    fi
    if [ -z "$OUT" ]; then
        # numeric scalar fallback (defaultPort, minRamGb…)
        OUT="$(sed -n "s/.*\"${KEYPATH##*.}\"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p" "$FILE" 2>/dev/null | head -n1)"
    fi
    printf '%s' "$OUT"
}

# A flat JSON array of strings as space-separated words (installer.json's
# imageKeys — the closed vocabulary of manifest image slots). Empty when the
# file or key is missing; callers warn and fall back.
# Usage: json_keys FILE KEY
json_keys() {
    local FILE="$1" KEY="$2" OUT=""
    [ -f "$FILE" ] || { printf ''; return 0; }
    if command -v python3 &>/dev/null; then
        OUT="$(python3 -c '
import json, sys
try:
    node = json.load(open(sys.argv[1])).get(sys.argv[2])
except Exception:
    sys.exit(0)
if isinstance(node, list):
    print(" ".join(str(v) for v in node))
' "$FILE" "$KEY" 2>/dev/null || true)"
    fi
    if [ -z "$OUT" ]; then
        OUT="$(sed -n "s/.*\"$KEY\"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p" "$FILE" 2>/dev/null \
            | tr -d '"' | tr ',' ' ')"
    fi
    printf '%s' "$OUT"
}

# ── Published-port pre-flight ───────────────────────────────────────────────
# A bind conflict (6379 and friends) otherwise surfaces mid-startup as a raw
# daemon error, after the pull, with no hint about which knob to turn. The
# port set is read from the RENDERED compose model — the only source that
# knows about bind addresses, ranges and services added later — so this runs
# after .env exists (compose interpolates it).

# Ports the stack would publish, as "<service>\t<port>" lines. Empty when the
# model cannot be rendered (compose v1, broken interpolation) — callers
# treat empty as "skip".
rendered_ports() {
    run_compose config --format json 2>/dev/null | python3 -c '
import json, sys
try:
    model = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for name, svc in (model.get("services") or {}).items():
    for spec in svc.get("ports") or []:
        published = spec.get("published")
        if not published:
            continue
        # compose may render "<ip>:<port>" (bind address) or "<port>";
        # a range "a-b" publishes every port in it.
        for port in str(published).split(":")[-1].split("-"):
            if port:
                print("%s\t%s" % (name, port))
' 2>/dev/null || true
}

# True when something accepts IPv4 connections on this host port. Loopback is
# where the standalone stack binds (127.0.0.1) and also covers 0.0.0.0.
port_in_use() {
    python3 - "$1" <<'PY'
import socket, sys
sock = socket.socket()
sock.settimeout(0.3)
try:
    sock.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    sock.close()
sys.exit(0)
PY
}

# True when every container publishing this port belongs to OUR compose
# project — the normal re-run case, which must not read as a conflict.
port_owner_is_project() {
    local PROJECTS
    PROJECTS="$(docker ps --filter "publish=$1" \
        --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null \
        | sed '/^$/d' | sort -u || true)"
    [ -n "$PROJECTS" ] && [ "$PROJECTS" = "$2" ]
}

# The .env variable a published port comes from. Matched by VALUE — the
# variable's current .env value, else its compose default — because the
# rendered port is the overridden one ("${REDIS_PORT:-6379}" publishes 6380
# when .env says 6380). Empty for literals ("80:80"), which can only be
# changed in the compose file itself.
port_env_var() {
    local PORT="$1" VAR VALUE DEFAULT
    # shellcheck disable=SC2013  # word-split list of compose env names is intended
    for VAR in $(grep -oE '\$\{[A-Z][A-Z0-9_]*(:-[0-9]+)?\}' "$COMPOSE_FILE" 2>/dev/null \
        | sed -E 's/^\$\{([A-Z][A-Z0-9_]*).*/\1/' | sort -u); do
        VALUE="$(env_value "$VAR" "")"
        if [ -n "$VALUE" ] && [ "$VALUE" = "$PORT" ]; then
            printf '%s' "$VAR"
            return 0
        fi
        if [ -z "$VALUE" ]; then
            DEFAULT="$(sed -n "s/.*\${${VAR}:-\([0-9][0-9]*\)}.*/\1/p" "$COMPOSE_FILE" | head -n1)"
            if [ "$DEFAULT" = "$PORT" ]; then
                printf '%s' "$VAR"
                return 0
            fi
        fi
    done
    return 1
}

next_free_port() {
    local PORT="$1" TRIES=0
    while [ "$TRIES" -lt 50 ] && [ "$PORT" -lt 65535 ]; do
        PORT=$((PORT + 1))
        port_in_use "$PORT" || { printf '%s' "$PORT"; return 0; }
        TRIES=$((TRIES + 1))
    done
    return 1
}

# Fail fast, before anything is pulled or started, when a published host port
# is already taken by something that is not this stack. --auto advances to a
# free port and writes it into .env instead of stopping.
# Exit code 4 = port conflict (the installer contract's code for exactly
# this failure, so operators can script around it).
# Usage: check_published_ports [--auto]
check_published_ports() {
    local AUTO=0
    if [ "${1:-}" = "--auto" ]; then AUTO=1; fi
    local PROJECT LIST SVC PORT VAR FREE CONFLICTS=""
    PROJECT="$(resolve_compose_project)"
    LIST="$(rendered_ports)"
    if [ -z "$LIST" ]; then
        echo -e "${YELLOW}Port pre-flight: could not render the compose model — skipping.${NC}"
        return 0
    fi
    while IFS=$'\t' read -r SVC PORT; do
        [ -n "$PORT" ] || continue
        port_in_use "$PORT" || continue
        port_owner_is_project "$PORT" "$PROJECT" && continue
        VAR="$(port_env_var "$PORT" || true)"
        if [ "$AUTO" = "1" ] && [ -n "$VAR" ]; then
            FREE="$(next_free_port "$PORT")" || FREE=""
            if [ -n "$FREE" ]; then
                set_env_value "$VAR" "$FREE"
                echo -e "${GREEN}Port ${PORT} was busy — ${VAR}=${FREE} written to .env.${NC}"
                continue
            fi
        fi
        if [ -n "$VAR" ]; then
            CONFLICTS="${CONFLICTS}  ${SVC}: host port ${PORT} is in use → set ${VAR}=<free port> in .env"$'\n'
        else
            CONFLICTS="${CONFLICTS}  ${SVC}: host port ${PORT} is in use and is a literal in ${COMPOSE_FILE} → edit it there"$'\n'
        fi
    done <<< "$LIST"
    if [ -n "$CONFLICTS" ]; then
        echo -e "${RED}"
        echo "Port conflict — the stack cannot start:"
        printf '%s' "$CONFLICTS"
        echo -e "${NC}"
        echo "  Find the holder:  ss -ltnp | grep ':<port>'   (or: lsof -i :<port>')"
        echo "  Then re-run this script — it reuses your .env."
        echo "  Or let the installer pick free ports: --auto-ports"
        echo -e "${RED}Aborted before pulling or starting anything.${NC}"
        exit 4
    fi
    echo -e "${GREEN}Port pre-flight: no conflicts.${NC}"
}

# ── compose project / volumes ────────────────────────────────────────────────

# Resolve the compose project name so we can reason about named volumes.
# Fallback: the pinned top-level name in the compose file.
resolve_compose_project() {
    local PROJECT
    PROJECT="$(run_compose config --format json 2>/dev/null \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("name",""))' 2>/dev/null || true)"
    [ -z "$PROJECT" ] && PROJECT="health-assistant"
    printf '%s' "$PROJECT"
}

# Adopt volumes created before the compose files pinned an explicit top-level
# `name:` — those defaulted to the compose file's directory ("docker"), so
# real data can sit in docker_postgres_data / docker_uploads. Docker has no
# `volume rename`, so adoption stops the legacy project when it still holds
# the volume and COPIES the data into a correctly-labeled new volume (the
# stack's own Postgres image runs the copy — no extra image dependency; the
# tag is PINNED to the same image the compose files use, never a floating
# `latest-*` tag). The target is only ever left behind by a successful copy
# (rollback on failure), so a retry can never mistake an empty volume for
# migrated data. No-op once adopted.
adopt_legacy_volumes() {
    local PROJECT VOL SRC DST
    local PINNED_TS="timescale/timescaledb:2.32.0-pg16"
    PROJECT="$(resolve_compose_project)"
    [ "$PROJECT" = "docker" ] && return 0
    for VOL in postgres_data redis_data uploads; do
        SRC="docker_${VOL}"
        DST="${PROJECT}_${VOL}"
        docker volume inspect "$SRC" >/dev/null 2>&1 || continue
        docker volume inspect "$DST" >/dev/null 2>&1 && continue
        # Stop the legacy project first (volumes are preserved by `down`) so
        # the copy is consistent and nothing holds the source volume.
        run_compose -p docker down >/dev/null 2>&1 || true
        if [ -n "$(docker ps -q --filter "volume=$SRC")" ]; then
            die "Legacy volume ${SRC} is still in use — stop that stack manually and re-run."
        fi
        docker volume create \
            --label "com.docker.compose.project=${PROJECT}" \
            --label "com.docker.compose.volume=${VOL}" \
            "$DST" >/dev/null
        if docker run --rm --entrypoint sh -v "${SRC}":/from:ro -v "${DST}":/to \
                "$PINNED_TS" -c 'cp -a /from/. /to/' >/dev/null 2>&1; then
            echo -e "${GREEN}Adopted legacy volume ${SRC} → ${DST}${NC}"
            docker volume rm "$SRC" >/dev/null 2>&1 \
                || echo -e "${YELLOW}Copied ${SRC} → ${DST} but could not remove the legacy volume (still attached?) — remove it manually.${NC}"
        else
            docker volume rm "$DST" >/dev/null 2>&1 || true
            die "Failed to copy ${SRC} → ${DST} (target removed; legacy data untouched). Pull ${PINNED_TS} or migrate manually — see docker/README.md → \"Compose project name & volumes\"."
        fi
    done
}

# One-time database/role rename for the ADR-0022 amendment (2026-09-30):
# legacy `neuro_health*` (and the pre-ADR `health_assistant`) names →
# `neuronection_health*`. Existing volumes ignore POSTGRES_DB after first
# boot, so postgres is started first and the rename runs inside it.
# PostgreSQL refuses to rename the session's own user, so the owner role
# is renamed through a throwaway superuser. No-op on fresh installs
# (the legacy names simply don't exist); a connected bootstrap role is
# required, else we fail loud with the manual recipe (docker/README.md →
# "Compose project name & volumes").
migrate_legacy_db_names() {
    local NEW_DB="neuronection_health" ROLE OLD_DB OLD_EXISTS NEW_EXISTS CONNECTED=""
    local PSQL
    run_compose up -d postgres >/dev/null \
        || die "Could not start postgres to check legacy database names."
    for _ in $(seq 1 30); do
        for ROLE in neuronection_health_owner neuro_health_owner admin; do
            if run_compose exec -T postgres \
                    psql -U "$ROLE" -d postgres -tAc "select 1" >/dev/null 2>&1; then
                CONNECTED="$ROLE"
                break 2
            fi
        done
        sleep 2
    done
    [ -z "$CONNECTED" ] && die "Cannot connect to postgres to check legacy database names — see docker/README.md → \"Compose project name & volumes\"."
    PSQL="run_compose exec -T postgres psql -U $CONNECTED -d postgres -v ON_ERROR_STOP=1"

    for OLD_DB in neuro_health health_assistant; do
        [ "$OLD_DB" = "$NEW_DB" ] && continue
        OLD_EXISTS="$($PSQL -tAc "select 1 from pg_database where datname='$OLD_DB'")"
        [ "$OLD_EXISTS" = "1" ] || continue
        NEW_EXISTS="$($PSQL -tAc "select 1 from pg_database where datname='$NEW_DB'")"
        [ "$NEW_EXISTS" = "1" ] && die "Both ${OLD_DB} and ${NEW_DB} exist — resolve manually (dump the old one, restore into ${NEW_DB}) before re-running."
        $PSQL -c "ALTER DATABASE \"${OLD_DB}\" RENAME TO \"${NEW_DB}\";" >/dev/null
        echo -e "${GREEN}Renamed database ${OLD_DB} → ${NEW_DB}${NC}"
    done

    if [ "$($PSQL -tAc "select 1 from pg_roles where rolname='neuro_health_app'")" = "1" ] \
        && [ "$($PSQL -tAc "select 1 from pg_roles where rolname='neuronection_health_app'")" != "1" ]; then
        $PSQL -c "ALTER ROLE neuro_health_app RENAME TO neuronection_health_app;" >/dev/null
        echo -e "${GREEN}Renamed role neuro_health_app → neuronection_health_app${NC}"
    fi

    if [ "$($PSQL -tAc "select 1 from pg_roles where rolname='neuro_health_owner'")" = "1" ]; then
        [ "$($PSQL -tAc "select 1 from pg_roles where rolname='neuronection_health_owner'")" = "1" ] \
            && die "Both neuro_health_owner and neuronection_health_owner exist — resolve manually before re-running."
        $PSQL -c "DROP ROLE IF EXISTS ha_db_migrator;" >/dev/null
        $PSQL -c "CREATE ROLE ha_db_migrator LOGIN SUPERUSER;" >/dev/null
        run_compose exec -T postgres \
            psql -U ha_db_migrator -d postgres -v ON_ERROR_STOP=1 \
            -c "ALTER ROLE neuro_health_owner RENAME TO neuronection_health_owner;" >/dev/null
        run_compose exec -T postgres \
            psql -U neuronection_health_owner -d postgres -v ON_ERROR_STOP=1 \
            -c "DROP ROLE ha_db_migrator;" >/dev/null
        echo -e "${GREEN}Renamed role neuro_health_owner → neuronection_health_owner${NC}"
    fi
    $PSQL -c "DROP ROLE IF EXISTS ha_db_migrator;" >/dev/null 2>&1 || true
}

# Early upgrade pre-flight (v0.9.0): refuse a pre-0.9 database or a stale
# v0.8.0 .env before images are pulled or volumes are recreated. The hard
# gate also lives in alembic/env.py; this is the actionable early stop.
run_upgrade_preflight() {
    bash "$LIB_DIR/upgrade-check.sh" \
        || die "Upgrade pre-flight failed — resolve the item(s) above before continuing."
}

# Leftover-volume guard — call after freshly (re)generating .env.
#
# On a fresh host, a leftover Postgres volume from a *previous* install on
# this Docker host silently survives `docker compose up -d`. The postgres
# container only applies POSTGRES_PASSWORD when the data dir is empty — once a
# volume is initialized it keeps the OLD password, so the freshly generated
# .env's new password makes `alembic upgrade head` (and the backend) fail with
# "password authentication failed for user neuronection_health_owner".
#
# Usage: check_leftover_db_volume "$ENV_WAS_FRESH"
#   ENV_WAS_FRESH=1 → this install just minted new credentials; if the compose
#   project's postgres volume already exists, offer to reset it (destructive)
#   or abort so the user can restore their previous .env.
check_leftover_db_volume() {
    [ "$1" = "1" ] || return 0
    local PROJECT PG_VOL RESET
    PROJECT="$(resolve_compose_project)"
    PG_VOL="${PROJECT}_postgres_data"
    if docker volume inspect "$PG_VOL" >/dev/null 2>&1; then
        echo -e "${YELLOW}"
        echo -e "${YELLOW}Leftover database volume detected: ${PG_VOL}${NC}"
        echo -e "${YELLOW}It was initialized by a previous install with a DIFFERENT password than the"
        echo -e "${YELLOW}.env just generated. Starting now would fail with \"password authentication"
        echo -e "${YELLOW}failed for user neuronection_health_owner\" in the migrate step.${NC}"
        read -r -p "$(echo -e 'Reset this volume for a clean fresh install? (destructive) [y/N]: ')" RESET || RESET=""
        if [[ "$RESET" =~ ^[Yy] ]]; then
            if ! docker volume rm "$PG_VOL" >/dev/null 2>&1; then
                # Volume in use by a running stack from the previous install —
                # bring it down (volumes are preserved by `down`, then removed).
                echo -e "${YELLOW}Volume in use — stopping the previous stack first...${NC}"
                run_compose down >/dev/null 2>&1 || true
                docker volume rm "$PG_VOL" >/dev/null 2>&1 \
                    || die "Could not remove ${PG_VOL}. Stop the old stack manually and re-run."
            fi
            echo -e "${GREEN}Volume removed — starting from a clean database.${NC}"
        else
            die "Aborted. Restore your previous .env (it holds the matching POSTGRES_PASSWORD) and re-run, or remove the volume manually: docker volume rm ${PG_VOL}"
        fi
    fi
}

# Force-refresh data volumes (install.sh --reset).
#
# Stops the stack and removes the compose project's named data volumes
# (postgres_data + redis_data; uploads only with --reset-all), so the next
# `up -d` starts from empty storage. Unlike check_leftover_db_volume this is
# unconditional — it fires even when .env already exists, covering the case of
# an old install whose volumes are stale/corrupt but whose .env is kept.
#
# Usage: reset_stack_data [--yes] [--all]
#   --yes  skip the confirmation prompt
#   --all  also remove the uploads volume (user files)
reset_stack_data() {
    local PROJECT PG_VOL REDIS_VOL UPLOADS_VOL ASSUME_YES=0 INCLUDE_UPLOADS=0 arg
    for arg in "$@"; do
        case "$arg" in
            --yes) ASSUME_YES=1 ;;
            --all) INCLUDE_UPLOADS=1 ;;
        esac
    done
    PROJECT="$(resolve_compose_project)"
    PG_VOL="${PROJECT}_postgres_data"
    REDIS_VOL="${PROJECT}_redis_data"
    UPLOADS_VOL="${PROJECT}_uploads"

    echo
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}  THIS WILL PERMANENTLY DELETE THE STACK'S DATA${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  Project:   ${PROJECT}"
    echo -e "  Volumes:   ${RED}${PG_VOL}${NC}, ${RED}${REDIS_VOL}${NC}"
    if [ "$INCLUDE_UPLOADS" = "1" ]; then
        echo -e "             ${RED}${UPLOADS_VOL}${NC} (--reset-all)"
    else
        echo -e "             ${UPLOADS_VOL} preserved (use --reset-all to wipe user files)"
    fi
    echo -e "  .env:      kept (never overwritten)"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    if [ "$ASSUME_YES" != "1" ]; then
        if ! read -r -p "Type 'yes' to confirm and reset: " REPLY; then
            die "--reset needs --yes when not run interactively."
        fi
        [ "$REPLY" = "yes" ] || die "Aborted — nothing was changed."
    fi

    # Stop the stack first (volumes in use can't be removed). `down` without -v
    # preserves named volumes; we remove them explicitly below.
    echo -e "${YELLOW}Stopping the stack...${NC}"
    run_compose down >/dev/null 2>&1 || true

    for VOL in "$PG_VOL" "$REDIS_VOL" $([ "$INCLUDE_UPLOADS" = "1" ] && echo "$UPLOADS_VOL"); do
        if docker volume inspect "$VOL" >/dev/null 2>&1; then
            docker volume rm "$VOL" >/dev/null 2>&1 \
                && echo -e "${GREEN}Removed ${VOL}${NC}" \
                || echo -e "${YELLOW}Could not remove ${VOL} (left in place)${NC}"
        else
            echo -e "${GREEN}${VOL} did not exist — nothing to remove.${NC}"
        fi
    done
    echo -e "${GREEN}Data reset complete — the stack will start from empty storage.${NC}"
}

# ── stack state reporting ────────────────────────────────────────────────────

# One line per stack service: "<service>|<state>|<status>". `ps -a` so a
# one-shot (migrate) that already exited is visible; falls back to the plain
# table on compose v1.
compose_service_states() {
    run_compose ps -a \
        --format '{{.Service}}|{{.State}}|{{.Status}}' 2>/dev/null \
        || run_compose ps -a 2>/dev/null
}

# "name (exit N)" for every container of this project that exited non-zero —
# a one-shot that finished clean (migrate) is NOT a failure.
failed_services() {
    local PROJECT ID STATE CODE NAME
    PROJECT="$(resolve_compose_project)"
    for ID in $(docker ps -a --filter "label=com.docker.compose.project=${PROJECT}" -q 2>/dev/null); do
        STATE="$(docker inspect -f '{{.State.Status}}' "$ID" 2>/dev/null)"
        [ "$STATE" = "exited" ] || continue
        CODE="$(docker inspect -f '{{.State.ExitCode}}' "$ID" 2>/dev/null)"
        [ "$CODE" = "0" ] && continue
        NAME="$(docker inspect -f '{{.Name}}' "$ID" 2>/dev/null | sed 's#^/##')"
        printf '%s (exit %s)\n' "${NAME:-$ID}" "${CODE:-?}"
    done
}

# Print what the stack actually looks like plus the failed containers' last
# lines, so a failed boot names its culprit instead of pointing at the backend
# while redis is the thing that died.
report_stack_failure() {
    echo -e "${YELLOW}Compose services:${NC}"
    compose_service_states | sed 's/^/  /'
    local FAILED NAME
    FAILED="$(failed_services)"
    if [ -n "$FAILED" ]; then
        echo -e "${RED}Failed containers:${NC}"
        printf '%s\n' "$FAILED" | sed 's/^/  /'
        echo
        while read -r NAME; do
            [ -n "$NAME" ] || continue
            echo -e "${YELLOW}Last log lines — ${NAME%% *}:${NC}"
            docker logs --tail 15 "${NAME%% *}" 2>&1 | sed 's/^/  /'
            echo
        done <<< "$FAILED"
    fi
}

# ── health / readiness waits ─────────────────────────────────────────────────

# Wait for the backend healthcheck (container name is hardcoded by the
# standalone compose file, so this works regardless of the compose project
# name). The container healthcheck probes /health internally. Fails fast when
# a service has already died, and on timeout reports the whole stack — never
# just "check the backend logs". HEALTH_WAIT_INTERVAL overrides the poll
# interval; HEALTH_WAIT_TIMEOUT the window (tests).
wait_for_backend_healthy() {
    local timeout="${1:-${HEALTH_WAIT_TIMEOUT:-180}}"
    local interval="${HEALTH_WAIT_INTERVAL:-5}"
    local elapsed=0 status failed
    echo -e "${YELLOW}Waiting for the backend to become healthy (up to ${timeout}s)...${NC}"
    while [ "$elapsed" -lt "$timeout" ]; do
        status=$(docker inspect -f '{{.State.Health.Status}}' health-assistant-backend 2>/dev/null || echo "not_found")
        if [ "$status" = "healthy" ]; then
            echo -e "${GREEN}Backend is healthy (/health).${NC}"
            return 0
        fi
        failed="$(failed_services)"
        if [ -n "$failed" ]; then
            echo
            echo -e "${RED}The stack stopped before the backend became healthy.${NC}"
            report_stack_failure
            echo -e "${YELLOW}Fix the failure above, then re-run this script — it reuses .env.${NC}"
            return 1
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    echo
    echo -e "${RED}Timed out waiting for the backend to become healthy.${NC}"
    report_stack_failure
    echo -e "${YELLOW}Next steps:${NC}"
    echo "  bind conflict → set the named <VAR> in .env, then re-run"
    echo "  migrate failed → run_compose logs migrate"
    echo "  backend slow   → run_compose logs backend --tail=100"
    return 1
}

# Wait for /ready (installer.json readyPath) through the published port —
# the readiness probe proves the migrate one-shot finished and the DB is at
# this release's head, so ingress may send traffic. 200-ready only; a 503
# after the wait window fails like the health wait (exit 3 at the caller).
# READY_WAIT_INTERVAL / READY_WAIT_ATTEMPTS override for tests.
# Usage: wait_for_ready URL [HOST]
# HOST is the APP_URL hostname; the probe goes through the published port on
# 127.0.0.1, so production's TrustedHostMiddleware needs the app's own host
# (it rejects a bare 127.0.0.1 Host header with 400). Ports are ignored by the
# matcher; pass the hostname only.
wait_for_ready() {
    local url="$1"
    local host="${2:-}"
    local interval="${READY_WAIT_INTERVAL:-5}"
    local attempts="${READY_WAIT_ATTEMPTS:-24}"
    local -a curl_args=(-fsS)
    [ -n "$url" ] || return 0
    [ -n "$host" ] && curl_args+=(-H "Host: ${host}")
    echo -e "${YELLOW}Waiting for readiness at ${url} (up to $((attempts * interval))s)...${NC}"
    for _ in $(seq 1 "$attempts"); do
        if curl "${curl_args[@]}" "$url" 2>/dev/null | grep -q '"ready"'; then
            echo -e "${GREEN}App is ready: ${url}${NC}"
            return 0
        fi
        sleep "$interval"
    done
    echo -e "${RED}App did not report ready at ${url} within $((attempts * interval))s${NC}" >&2
    echo -e "${YELLOW}The database may still be migrating — check: run_compose logs migrate${NC}" >&2
    return 1
}
