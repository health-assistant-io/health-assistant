#!/bin/bash

# Health Assistant — one-command Docker install
#
# Takes a fresh clone to a running standalone stack: pre-flight → generate
# .env (interactive, Quick Start default) → docker compose up -d → wait for
# backend healthy → summary.
#
# Usage:
#   ./scripts/install.sh             # install + start the standalone stack
#   ./scripts/install.sh --env-only  # generate .env only, don't start
#   ./scripts/install.sh --no-wait   # skip the backend health-wait
#   ./scripts/install.sh -h|--help   # print this help and exit
#
# Idempotent: safe to re-run after `git pull` — compose up -d is a no-op when
# the stack is already running and .env is never overwritten.
#
# Windows: requires WSL2 or Git-Bash (bash).

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
        --env-only) ENV_ONLY=1 ;;
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

# Generate .env on first run only (setup_env.py refuses to overwrite an
# existing one).
if [ ! -f ".env" ]; then
    if ! command -v python3 &> /dev/null; then
        die "python3 is required to generate .env. Install Python 3, or copy .env.example to .env and edit it manually."
    fi
    echo -e "${GREEN}No .env found — generating one now (Quick Start is the default).${NC}"
    python3 scripts/setup_env.py
    if [ $? -ne 0 ]; then
        die "Environment setup failed — fix the errors above and re-run."
    fi
else
    echo -e "${GREEN}.env already exists — leaving it untouched.${NC}"
fi

if [ -n "$ENV_ONLY" ]; then
    echo -e "${GREEN}Done — .env is ready. Start the stack with:${NC}"
    echo "  $DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS up -d"
    exit 0
fi

echo -e "${GREEN}Starting the standalone stack...${NC}"
$DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS up -d

if [ -z "$NO_WAIT" ]; then
    wait_for_backend_healthy || exit 1
fi

# ── Summary ──────────────────────────────────────────────────────────────────
APP_URL=$(grep -E '^APP_URL=' .env | head -n1 | cut -d= -f2-)
SETUP_TOKEN_MODE=$(grep -E '^SETUP_TOKEN_MODE=' .env | head -n1 | cut -d= -f2-)
BOOTSTRAP_TOKEN=$(grep -E '^SETUP_BOOTSTRAP_TOKEN=' .env | head -n1 | cut -d= -f2-)
[ -z "$APP_URL" ] && APP_URL="http://localhost"

echo
echo -e "${GREEN}======================================================${NC}"
echo -e "${GREEN}   Health Assistant is running!${NC}"
echo -e "${GREEN}======================================================${NC}"
echo "  App:     $APP_URL"
if [ "$SETUP_TOKEN_MODE" = "env" ] && [ -n "$BOOTSTRAP_TOKEN" ]; then
    echo "  Setup:   $APP_URL/setup?token=$BOOTSTRAP_TOKEN"
    echo "           (one-click first-run setup — create your admin account)"
else
    echo "  Setup:   open $APP_URL and use the setup wizard"
    echo "           (non-localhost installs need the setup token from:)"
    echo "           $DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS logs backend | grep -i -A 1 \"setup token\""
fi
echo "  Health:  $APP_URL/health"
echo "  Flower:  $APP_URL/flower/  (Celery task monitor; FLOWER_USER/FLOWER_PASSWORD)"
if ! grep -qE '^OPENAI_API_KEY=.+' .env; then
    echo
    echo "  Note:    no OPENAI_API_KEY set — AI features (OCR, extraction, chat)"
    echo "           need an AI provider key configured in-app (System Admin → AI)"
    echo "           or via the OPENAI_* env vars. The app runs fine without one."
fi
echo
echo -e "${YELLOW}Updates: run ./scripts/update-docker.sh${NC}"