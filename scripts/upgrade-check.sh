#!/bin/bash

# Health Assistant — upgrade pre-flight
#
# Read-only check that an existing install can move to this release. It
# refuses the two v0.9.0 breaking changes that otherwise surface as cryptic
# failures halfway through an update:
#
#   * a v0.8.0 (or earlier) database — the migration chain was squash-replaced
#     by 0001_baseline, so there is no in-place upgrade;
#   * a PostgreSQL 14 data volume — v0.9.0 runs PostgreSQL 16, and a major
#     version bump is not data-directory compatible;
#   * retired .env names from v0.8.0 (APP_ENV, SECRET_KEY, ...) that would be
#     silently ignored — APP_ENV=production in particular would boot
#     development defaults with ephemeral keys.
#
# install.sh and update-docker.sh run this before pulling images or touching
# volumes; the same refusal is enforced inside alembic/env.py for every other
# path (compose migrate, backup restore, bare CLI).
#
# Usage:
#   ./scripts/upgrade-check.sh              # .env + running DB (if reachable)
#   ./scripts/upgrade-check.sh --env-only   # .env only, no Docker needed
#   ./scripts/upgrade-check.sh --reset-empty
#                                           # also show the safe reset command
#                                           # when the database has no users
#   ./scripts/upgrade-check.sh -h|--help    # print this help and exit
#
# Exit: 0 no blockers; 1 at least one blocker must be resolved. This script
# never writes, pulls, starts or deletes anything.

print_help() {
  sed -n '3,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit 0
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib-docker.sh"

ENV_ONLY=0
RESET_EMPTY=0
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -h|--help) print_help ;;
        --env-only) ENV_ONLY=1 ;;
        --reset-empty) RESET_EMPTY=1 ;;
        *)
            echo -e "${RED}Unknown parameter: $1 (try --help)${NC}" >&2
            exit 1
            ;;
    esac
    shift
done

BLOCKERS=0
WARNINGS=0
OLD_REVISION=""
DATABASE_NAME=""

blocker() {
    echo -e "${RED}[blocked]${NC} $1"
    BLOCKERS=$((BLOCKERS + 1))
}

warning() {
    echo -e "${YELLOW}[warn]${NC} $1"
    WARNINGS=$((WARNINGS + 1))
}

fine() {
    echo -e "${GREEN}[ok]${NC} $1"
}

# A setting is configured when .env has it OR the launcher exported it —
# compose interpolates exported values over .env, so the pre-flight must too.
configured_value() {
    local value
    value="$(env_value "$1" "")"
    [ -n "$value" ] || value="${!1:-}"
    printf '%s' "$value"
}

# ── retired env names ───────────────────────────────────────────────────────
# Every Settings field that v0.8.0 read and v0.9.0 renamed or retired. The
# dangerous ones block (they silently downgrade or break sealed secrets);
# the rest warn so the .env can be cleaned up.
check_env_names() {
    local name replacement value
    while IFS='|' read -r name replacement; do
        [ -n "$name" ] || continue
        grep -qE "^${name}=" .env 2>/dev/null || continue
        value="$(env_value "$name" "")"
        case "$name" in
            APP_ENV)
                if [ -n "$(configured_value HA_APP_ENV)" ]; then
                    warning "APP_ENV is no longer read — remove it now that HA_APP_ENV is set."
                else
                    case "$value" in
                        production|prod|staging)
                            blocker "APP_ENV=${value} is no longer read (v0.9.0 renamed it to HA_APP_ENV). Left as-is, the app boots development defaults — ephemeral keys, no production guards — and sealed credentials stop decrypting. Rename the line to HA_APP_ENV=${value}."
                            ;;
                        *)
                            warning "APP_ENV is no longer read — rename it to HA_APP_ENV (same value)."
                            ;;
                    esac
                fi
                ;;
            SECRET_KEY)
                if [ -z "$(configured_value HA_SESSION_KEY)" ] || [ -z "$(configured_value HA_REFRESH_KEY)" ]; then
                    blocker "SECRET_KEY is retired and no HA_SESSION_KEY/HA_REFRESH_KEY pair is set. Generate two independent keys (setup_env.py does this) — the old value is not reused for either."
                else
                    warning "SECRET_KEY is retired and ignored — remove it now that HA_SESSION_KEY/HA_REFRESH_KEY are set."
                fi
                ;;
            INTEGRATION_SECRET_KEY)
                if [ -z "$(configured_value HA_DATA_KEY)" ]; then
                    blocker "INTEGRATION_SECRET_KEY is retired and HA_DATA_KEY is unset. Copy the SAME value to HA_DATA_KEY so sealed credentials keep decrypting, then remove the old line."
                else
                    warning "INTEGRATION_SECRET_KEY is retired and ignored — HA_DATA_KEY is already set; remove the old line."
                fi
                ;;
            INTEGRATION_SECRET_KEY_PREVIOUS)
                warning "INTEGRATION_SECRET_KEY_PREVIOUS is no longer read — rename it to HA_DATA_KEY_PREVIOUS (decrypt-only rotation ring)."
                ;;
            *)
                warning "${name} is no longer read — use ${replacement} instead."
                ;;
        esac
    done <<'EOF'
APP_ENV|HA_APP_ENV
SECRET_KEY|HA_SESSION_KEY / HA_REFRESH_KEY / HA_DATA_KEY
INTEGRATION_SECRET_KEY|HA_DATA_KEY
INTEGRATION_SECRET_KEY_PREVIOUS|HA_DATA_KEY_PREVIOUS
DATABASE_URL|HA_DATABASE_URL
UPLOAD_DIR|HA_UPLOAD_DIR
DEMO_MODE|HA_DEMO_MODE
DEMO_USER_EMAIL|HA_DEMO_EMAIL
DEMO_USER_PASSWORD|HA_DEMO_PASSWORD
TRUSTED_PROXY_COUNT|HA_TRUSTED_PROXY_COUNT
JWT_EXPIRATION_HOURS|HA_AUTH_ACCESS_TTL_MINUTES (minutes, not hours)
OAUTH_ISSUER|(retired — iss is the product slug "health")
AI_CHECKPOINT_RETENTION_DAYS|(retired, no replacement)
EOF
}

# Revision ids present in this checkout's chain.
known_revisions() {
    grep -hE '^revision: str = ' backend/alembic/versions/*.py 2>/dev/null \
        | sed -E 's/^revision: str = "([^"]+)".*/\1/'
}

# ── running database ────────────────────────────────────────────────────────
# Best-effort: only inspects a stack that is already running. A fresh install
# (nothing running) is not a blocker — the migrate preflight still enforces
# the chain at start time.
check_database() {
    local role="" candidate="" version="" server_version="" exists users

    if ! command -v docker >/dev/null 2>&1; then
        warning "docker not found — database check skipped (the migrate preflight still enforces it)."
        return
    fi
    check_docker

    for candidate in "$(env_value POSTGRES_USER "")" neuronection_health_owner neuro_health_owner admin; do
        [ -n "$candidate" ] || continue
        if $DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS exec -T postgres \
                psql -U "$candidate" -d postgres -tAc "select 1" >/dev/null 2>&1; then
            role="$candidate"
            break
        fi
    done
    if [ -z "$role" ]; then
        warning "PostgreSQL is not running (or not reachable) — database check skipped; the migrate preflight enforces the chain at start time."
        return
    fi

    for candidate in "$(env_value POSTGRES_DB neuronection_health)" neuronection_health neuro_health health_assistant; do
        [ -n "$candidate" ] || continue
        exists="$($DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS exec -T postgres \
            psql -U "$role" -d postgres -tAc "select 1 from pg_database where datname='${candidate}'" 2>/dev/null | tr -d '[:space:]')"
        [ "$exists" = "1" ] && { DATABASE_NAME="$candidate"; break; }
    done
    if [ -z "$DATABASE_NAME" ]; then
        fine "no existing Health Assistant database — fresh install path"
        return
    fi

    version="$($DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS exec -T postgres \
        psql -U "$role" -d "$DATABASE_NAME" -tAc "select version_num from alembic_version" 2>/dev/null | tr -d '[:space:]')"
    if [ -z "$version" ]; then
        fine "database '${DATABASE_NAME}' is empty (no recorded migration)"
    elif grep -qx "$version" <<< "$(known_revisions)"; then
        fine "database '${DATABASE_NAME}' is at supported migration '${version}'"
    else
        OLD_REVISION="$version"
        blocker "database '${DATABASE_NAME}' records migration '${version}', which is not part of this release. v0.9.0 replaced the published v0.8.0 chain with 0001_baseline, so there is no in-place upgrade. Back up first, then follow docs/INSTALL.md -> Updates -> 'Upgrading from v0.8.0' (dump, fresh PostgreSQL 16 volume, restore)."
    fi

    server_version="$($DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS exec -T postgres \
        psql -U "$role" -d "$DATABASE_NAME" -tAc "show server_version_num" 2>/dev/null | tr -d '[:space:]')"
    if [ -n "$server_version" ] && [ "$server_version" -lt 160000 ] 2>/dev/null; then
        blocker "the running PostgreSQL is major version ${server_version:0:2}, but v0.9.0 runs PostgreSQL 16. Data directories are not compatible across majors: back up, recreate the volume, restore (docker/README.md -> 'Upgrading from pg14')."
    fi

    if [ "$RESET_EMPTY" = "1" ] && [ -n "$OLD_REVISION" ]; then
        users="$($DOCKER_COMPOSE_CMD $COMPOSE_ENV_ARGS exec -T postgres \
            psql -U "$role" -d "$DATABASE_NAME" -tAc "select count(*) from users" 2>/dev/null | tr -d '[:space:]')"
        if [ "$users" = "0" ]; then
            echo
            echo "This database has no users — discarding it loses nothing."
            echo "Safe reset: ./scripts/install.sh --reset --yes"
        elif [ -n "$users" ]; then
            echo
            echo "This database has ${users} user(s) — do not reset it. Back up and follow the runbook above."
        fi
    fi
}

echo -e "${GREEN}Health Assistant upgrade pre-flight${NC}"
check_cwd
require_env
check_env_names
if [ "$ENV_ONLY" = "0" ]; then
    check_database
fi

echo
if [ "$BLOCKERS" -gt 0 ]; then
    echo -e "${RED}Blocked: ${BLOCKERS} issue(s) must be resolved before this release can start.${NC}"
    exit 1
fi
if [ "$WARNINGS" -gt 0 ]; then
    echo -e "${YELLOW}No blockers — see the warning(s) above.${NC}"
else
    echo -e "${GREEN}No upgrade blockers found.${NC}"
fi
exit 0
