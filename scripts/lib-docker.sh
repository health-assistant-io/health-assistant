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

# Leftover-volume guard — call after freshly (re)generating .env.
#
# On a fresh clone, a leftover Postgres volume from a *previous* install on
# this Docker host silently survives `docker compose up -d`. The postgres
# container only applies POSTGRES_PASSWORD when the data dir is empty — once a
# volume is initialized it keeps the OLD password, so the freshly generated
# .env's new password makes `alembic upgrade head` (and the backend) fail with
# "password authentication failed for user admin".
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
        echo -e "${YELLOW}failed for user admin\" in the migrate step.${NC}"
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