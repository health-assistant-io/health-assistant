#!/usr/bin/env bash
#
# Health Assistant — uninstall (the family uninstall contract).
#
# Removes the running stack without touching your data by default:
#   default           compose down (containers + network); volumes, images,
#                     .env and the install dir all stay
#   --purge           also remove this project's named volumes (prints
#                     exactly what will die first and asks for confirmation)
#   --remove-dir      also remove the install directory (incl. .env);
#                     data lives in Docker volumes, so this alone destroys
#                     nothing — pair with --purge to wipe everything
#   --yes             non-interactive confirmation (CI/scripts)
#
# Works without a readable .env: teardown is by compose project
# label, not env values.
#
# Usage:
#   bash scripts/uninstall.sh                # stop + remove containers, keep data
#   bash scripts/uninstall.sh --purge        # also delete this project's volumes
#   bash scripts/uninstall.sh --purge --remove-dir --yes   # wipe everything
#   bash scripts/uninstall.sh -h|--help      # this help
#
# --reset (scripts/install.sh) is a different thing: a destructive
# reinstall that keeps the install dir. This script never reinstalls.

set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

print_help() {
    sed -n '3,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
    exit 0
}

die() { echo -e "${RED}uninstall: $*${NC}" >&2; exit 1; }

YES=0 PURGE=0 REMOVE_DIR=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) print_help ;;
        --yes) YES=1 ;;
        --purge) PURGE=1 ;;
        --remove-dir) REMOVE_DIR=1 ;;
        *) die "unknown flag: $1 (see --help)" ;;
    esac
    shift
done

command -v docker >/dev/null 2>&1 || die "docker is required"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(bash -c "source '${SCRIPT_DIR}/lib-docker.sh' && resolve_compose_project" 2>/dev/null || true)"
[ -n "$PROJECT" ] || PROJECT="health-assistant"

echo -e "${GREEN}Uninstalling Health Assistant (compose project: ${PROJECT}).${NC}"

# Label-based volume discovery: no .env read, no guessing.
VOLUMES="$(docker volume ls --filter "label=com.docker.compose.project=${PROJECT}" --format '{{.Name}}' 2>/dev/null || true)"

if [ "$PURGE" -eq 1 ]; then
    if [ -n "$VOLUMES" ]; then
        echo -e "${YELLOW}--purge will permanently delete these volumes:${NC}"
        printf '  %s\n' $VOLUMES
    else
        echo -e "${YELLOW}No project-labelled volumes found (nothing to purge).${NC}"
    fi
    if [ "$YES" -ne 1 ]; then
        printf '%s' "Type 'yes' to delete them: "
        if ! read -r REPLY; then die "confirmation failed"; fi
        [ "$REPLY" = "yes" ] || die "aborted — nothing deleted"
    fi
fi

docker compose -p "$PROJECT" down >/dev/null 2>&1 || true
echo -e "${GREEN}Stack stopped and containers removed (network removed if this project owned it).${NC}"

if [ "$PURGE" -eq 1 ] && [ -n "$VOLUMES" ]; then
    # shellcheck disable=SC2086
    docker volume rm $VOLUMES >/dev/null \
        && echo -e "${GREEN}Removed volumes: $(printf '%s ' $VOLUMES)${NC}" \
        || echo -e "${YELLOW}Some volumes could not be removed (in use?) — inspect: docker volume ls --filter label=com.docker.compose.project=${PROJECT}${NC}"
fi

if [ "$REMOVE_DIR" -eq 1 ]; then
    ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    if [ "$YES" -ne 1 ]; then
        echo -e "${YELLOW}--remove-dir will delete the install directory (incl. .env):${NC}"
        echo "  ${ROOT}"
        printf '%s' "Type 'yes' to delete it: "
        if ! read -r REPLY; then die "confirmation failed"; fi
        [ "$REPLY" = "yes" ] || die "aborted — directory kept"
    fi
    rm -rf "$ROOT"
    echo -e "${GREEN}Install directory removed.${NC}"
else
    echo "  Install dir and .env kept (backups/ too). Reinstall by re-running scripts/install.sh."
fi
if [ "$PURGE" -eq 0 ]; then
    echo "  Data volumes kept. To delete them too: bash scripts/uninstall.sh --purge"
fi
