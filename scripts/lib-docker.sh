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
    PROJECT="$($DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS config --format json 2>/dev/null \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("name",""))' 2>/dev/null || true)"
    # Fallback: the compose file lives in docker/, so the default project is "docker".
    [ -z "$PROJECT" ] && PROJECT="docker"
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