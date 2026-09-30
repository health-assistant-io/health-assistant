#!/bin/bash

# Health Assistant — shared helpers for the Docker install/update scripts.
#
# Sourced by scripts/install.sh and scripts/update-docker.sh; not meant to be
# run directly.

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

COMPOSE_FILE="docker/docker-compose.standalone.yml"
COMPOSE_ENV_ARGS="--env-file .env -f ${COMPOSE_FILE}"

die() {
    echo -e "${RED}Error: $1${NC}" >&2
    exit 1
}

check_cwd() {
    if [ ! -d "backend" ] || [ ! -d "frontend" ]; then
        die "Please run this script from the Health Assistant root directory"
    fi
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
    if [ ! -f ".env" ]; then
        die "'.env' file not found in project root. Run './scripts/install.sh' to generate one."
    fi
}

# Resolve the compose project name so we can reason about named volumes.
# Fallback: the compose file lives in docker/, so the default project is
# "docker" (matches reset-dev-db.sh).
resolve_compose_project() {
    local PROJECT
    PROJECT="$($DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS config --format json 2>/dev/null \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("name",""))' 2>/dev/null || true)"
    [ -z "$PROJECT" ] && PROJECT="docker"
    printf '%s' "$PROJECT"
}

# Adopt volumes created before the compose files pinned an explicit top-level
# `name:` — those defaulted to the compose file's directory ("docker"), so
# real data can sit in docker_postgres_data / docker_uploads. Docker has no
# `volume rename`, so adoption stops the legacy project when it still holds
# the volume and COPIES the data into a correctly-labeled new volume (the
# stack's own Postgres image runs the copy — no extra image dependency).
# The target is only ever left behind by a successful copy (rollback on
# failure), so a retry can never mistake an empty volume for migrated data.
# No-op once adopted.
adopt_legacy_volumes() {
    local PROJECT VOL SRC DST
    PROJECT="$(resolve_compose_project)"
    [ "$PROJECT" = "docker" ] && return 0
    for VOL in postgres_data redis_data uploads; do
        SRC="docker_${VOL}"
        DST="${PROJECT}_${VOL}"
        docker volume inspect "$SRC" >/dev/null 2>&1 || continue
        docker volume inspect "$DST" >/dev/null 2>&1 && continue
        # Stop the legacy project first (volumes are preserved by `down`) so
        # the copy is consistent and nothing holds the source volume.
        $DOCKER_COMPOSE_CMD -p docker $COMPOSE_ENV_ARGS down >/dev/null 2>&1 || true
        if [ -n "$(docker ps -q --filter "volume=$SRC")" ]; then
            die "Legacy volume ${SRC} is still in use — stop that stack manually and re-run."
        fi
        docker volume create \
            --label "com.docker.compose.project=${PROJECT}" \
            --label "com.docker.compose.volume=${VOL}" \
            "$DST" >/dev/null
        if docker run --rm --entrypoint sh -v "${SRC}":/from:ro -v "${DST}":/to \
                timescale/timescaledb:latest-pg16 -c 'cp -a /from/. /to/' >/dev/null 2>&1; then
            echo -e "${GREEN}Adopted legacy volume ${SRC} → ${DST}${NC}"
            docker volume rm "$SRC" >/dev/null 2>&1 \
                || echo -e "${YELLOW}Copied ${SRC} → ${DST} but could not remove the legacy volume (still attached?) — remove it manually.${NC}"
        else
            docker volume rm "$DST" >/dev/null 2>&1 || true
            die "Failed to copy ${SRC} → ${DST} (target removed; legacy data untouched). Pull timescale/timescaledb:latest-pg16 or migrate manually — see docker/README.md → \"Compose project name & volumes\"."
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
    $DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS up -d postgres >/dev/null \
        || die "Could not start postgres to check legacy database names."
    for _ in $(seq 1 30); do
        for ROLE in neuronection_health_owner neuro_health_owner admin; do
            if $DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS exec -T postgres \
                    psql -U "$ROLE" -d postgres -tAc "select 1" >/dev/null 2>&1; then
                CONNECTED="$ROLE"
                break 2
            fi
        done
        sleep 2
    done
    [ -z "$CONNECTED" ] && die "Cannot connect to postgres to check legacy database names — see docker/README.md → \"Compose project name & volumes\"."
    PSQL="$DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS exec -T postgres psql -U $CONNECTED -d postgres -v ON_ERROR_STOP=1"

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
        $DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS exec -T postgres \
            psql -U ha_db_migrator -d postgres -v ON_ERROR_STOP=1 \
            -c "ALTER ROLE neuro_health_owner RENAME TO neuronection_health_owner;" >/dev/null
        $DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS exec -T postgres \
            psql -U neuronection_health_owner -d postgres -v ON_ERROR_STOP=1 \
            -c "DROP ROLE ha_db_migrator;" >/dev/null
        echo -e "${GREEN}Renamed role neuro_health_owner → neuronection_health_owner${NC}"
    fi
    $PSQL -c "DROP ROLE IF EXISTS ha_db_migrator;" >/dev/null 2>&1 || true
}

# Leftover-volume guard — call after freshly (re)generating .env.
#
# On a fresh clone, a leftover Postgres volume from a *previous* install on
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
        read -r -p "$(echo -e 'Reset this volume for a clean fresh install? (destructive) [y/N]: ')" RESET
        if [[ "$RESET" =~ ^[Yy] ]]; then
            if ! docker volume rm "$PG_VOL" >/dev/null 2>&1; then
                # Volume in use by a running stack from the previous install —
                # bring it down (volumes are preserved by `down`, then removed).
                echo -e "${YELLOW}Volume in use — stopping the previous stack first...${NC}"
                $DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS down >/dev/null 2>&1 || true
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
        read -r -p "Type 'yes' to confirm and reset: " REPLY
        [ "$REPLY" = "yes" ] || die "Aborted — nothing was changed."
    fi

    # Stop the stack first (volumes in use can't be removed). `down` without -v
    # preserves named volumes; we remove them explicitly below.
    echo -e "${YELLOW}Stopping the stack...${NC}"
    $DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS down >/dev/null 2>&1 || true

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

# Wait for the backend healthcheck (container name is hardcoded by the
# standalone compose file, so this works regardless of the compose project
# name). Exits non-zero on timeout with a log hint.
wait_for_backend_healthy() {
    local timeout="${1:-180}"
    local interval=5
    local elapsed=0
    echo -e "${YELLOW}Waiting for the backend to become healthy (up to ${timeout}s)...${NC}"
    while [ "$elapsed" -lt "$timeout" ]; do
        local status
        status=$(docker inspect -f '{{.State.Health.Status}}' health-assistant-backend 2>/dev/null || echo "not_found")
        if [ "$status" = "healthy" ]; then
            echo -e "${GREEN}Backend is healthy.${NC}"
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    echo -e "${RED}Timed out waiting for the backend to become healthy.${NC}"
    echo -e "${YELLOW}Check the backend logs for errors:${NC}"
    echo "  $DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS logs backend --tail=100"
    return 1
}