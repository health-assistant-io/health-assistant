#!/bin/bash

# Health Assistant Docker Startup Script
#
# Build and launch the dev Docker stack defined in
# ``docker/docker-compose.dev.yml`` using the root ``.env`` (auto-detects
# ``docker compose`` vs the legacy ``docker-compose`` standalone binary).
# This is the dev-flavor stack (frontend served by Vite, hot reload, debug
# logging). For production see docs/INSTALL.md and use
# ``docker/docker-compose.standalone.yml`` / ``docker-compose.prod.yml``
# directly.
#
# Usage:
#   ./scripts/run-docker.sh             # build + up the dev stack
#   ./scripts/run-docker.sh -h|--help   # print this help and exit
#
# Run from the Health Assistant project root. Requires a populated ``.env``
# (run ``python scripts/setup_env.py`` first if missing). Exits non-zero on
# missing deps, a stopped Docker daemon, or a missing project layout.

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

print_help() {
  sed -n '3,18p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -h|--help) print_help ;;
        *)
            echo -e "${RED}Unknown parameter: $1 (try --help)${NC}" >&2
            exit 1
            ;;
    esac
done

echo "Starting Health Assistant with Docker..."

# Check if we're in the right directory
if [ ! -d "backend" ] || [ ! -d "frontend" ]; then
    echo -e "${RED}Error: Please run this script from the Health Assistant root directory${NC}"
    exit 1
fi

# Check if docker is installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed. Please install Docker first.${NC}"
    exit 1
fi

# Check if docker daemon is running
if ! docker info &> /dev/null; then
    echo -e "${RED}Error: Docker daemon is not running. Please start Docker first.${NC}"
    exit 1
fi

# Determine docker compose command (either 'docker compose' or 'docker-compose')
DOCKER_COMPOSE_CMD="docker compose"
if ! docker compose version &> /dev/null; then
    if command -v docker-compose &> /dev/null; then
        DOCKER_COMPOSE_CMD="docker-compose"
    else
        echo -e "${RED}Error: Docker Compose is not installed (neither 'docker compose' nor 'docker-compose' is available).${NC}"
        exit 1
    fi
fi

# Use the root .env directly — docker compose reads it via --env-file.
# (Previously this copied backend/.env to docker/.env and sed-rewrote the
#  port; with env consolidation the single root .env is the source of truth.)
if [ ! -f ".env" ]; then
    echo -e "${RED}Error: .env file not found in project root.${NC}"
    echo -e "${YELLOW}Run 'python scripts/setup_env.py' to generate one, or copy .env.example.${NC}"
    exit 1
fi

# Build and start services using docker-compose
echo -e "${GREEN}Building and launching Health Assistant containers...${NC}"
$DOCKER_COMPOSE_CMD -f docker/docker-compose.dev.yml --env-file .env up --build
