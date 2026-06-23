#!/bin/bash

# Health Assistant Docker Startup Script

echo "Starting Health Assistant with Docker..."

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

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

# Copy default docker env file if it doesn't exist
if [ ! -f "docker/.env" ]; then
    echo -e "${YELLOW}Warning: docker/.env file not found. Creating a default one...${NC}"
    if [ -f "backend/.env" ]; then
        cp backend/.env docker/.env
        # Update PG port for docker internal communication
        sed -i 's/POSTGRES_PORT=5433/POSTGRES_PORT=5432/g' docker/.env
    else
        echo -e "${RED}Error: Could not find backend/.env to use as template.${NC}"
        exit 1
    fi
fi

# Build and start services using docker-compose
echo -e "${GREEN}Building and launching Health Assistant containers...${NC}"
$DOCKER_COMPOSE_CMD -f docker/docker-compose.dev.yml --env-file docker/.env up --build
