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