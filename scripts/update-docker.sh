#!/bin/bash

# Health Assistant — one-command Docker update
#
# Updates an existing standalone Docker install: git pull (best-effort) →
# regenerate .env if missing → docker compose pull → up -d → wait for backend
# healthy.
#
# Usage:
#   ./scripts/update-docker.sh            # pull code, pull images, restart
#   ./scripts/update-docker.sh --no-pull  # don't git pull (refresh images only)
#   ./scripts/update-docker.sh --no-wait  # skip the backend health-wait
#   ./scripts/update-docker.sh -h|--help  # print this help and exit
#
# Idempotent: never bricks a running install — a failed git pull is a warning,
# not an error, and .env is never overwritten.

print_help() {
  sed -n '3,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit 0
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib-docker.sh"

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -h|--help) print_help ;;
        --no-pull) NO_PULL=1 ;;
        --no-wait) NO_WAIT=1 ;;
        *)
            echo -e "${RED}Unknown parameter: $1 (try --help)${NC}" >&2
            exit 1
            ;;
    esac
    shift
done

check_cwd
check_docker
require_env

if [ -z "$NO_PULL" ]; then
    echo -e "${GREEN}Pulling latest code...${NC}"
    if git pull --ff-only; then
        echo -e "${GREEN}Code updated.${NC}"
    else
        echo -e "${YELLOW}git pull failed (dirty tree or offline?) — continuing with local code.${NC}"
    fi
fi

# Regenerate .env if it was deleted (setup_env.py refuses to overwrite). Track
# freshness so the leftover-volume guard can flag a password mismatch.
ENV_WAS_FRESH=0
if [ ! -f ".env" ]; then
    python3 scripts/setup_env.py
    ENV_WAS_FRESH=1
fi

# Refuse a pre-0.9 database / stale v0.8.0 .env before pulling images or
# recreating volumes. Runs against the still-running stack when there is one;
# alembic/env.py enforces the same refusal for every other path.
run_upgrade_preflight

# Adopt volumes from before the compose files pinned their project name
# (docker_* → <project>_*), so an update attaches to the existing data
# instead of starting on empty volumes.
adopt_legacy_volumes

# Move a semver image pin forward with the tree (a pinned install refreshes via
# this script, not by jumping tags on every install.sh run). `latest`, branch
# names and custom tags are left untouched — those are deliberate choices.
if [ -f ".env" ]; then
    PINNED_TAG="$(env_value IMAGE_TAG "")"
    TREE_VER="$(tree_version)"
    case "$PINNED_TAG" in
        ""|latest|main|master) ;;
        *)
            if [[ "$PINNED_TAG" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+ ]] && [ -n "$TREE_VER" ] \
                && [ "${PINNED_TAG#v}" != "$TREE_VER" ]; then
                set_env_value IMAGE_TAG "$TREE_VER"
                export IMAGE_TAG="$TREE_VER"
                echo -e "${GREEN}Image pin moved ${PINNED_TAG} → ${TREE_VER} (this tree's version).${NC}"
            fi
            ;;
    esac
fi

echo -e "${GREEN}Pulling latest images...${NC}"
$DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS pull

# Leftover-volume guard: a freshly regenerated .env mints new credentials that
# can't match a leftover postgres_data volume from a previous install.
check_leftover_db_volume "$ENV_WAS_FRESH"

# One-time ADR-0022 DB/role rename (neuro_* → neuronection_*), before the
# stack boots against the new names. No-op on fresh installs.
migrate_legacy_db_names

echo -e "${GREEN}Restarting the stack...${NC}"
$DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS up -d

if [ -z "$NO_WAIT" ]; then
    wait_for_backend_healthy || exit 1
fi

echo -e "${GREEN}Update complete.${NC}"