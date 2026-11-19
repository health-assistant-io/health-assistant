#!/bin/bash

# Health Assistant — update compatibility alias
#
# The bundle updater is scripts/update.sh (the family installer contract:
# pin bump from the new release manifest, previous pin kept, pull, up -d,
# health/readiness wait — no git, no destructive operations). This old
# name stays so existing muscle memory and docs keep working; it forwards
# every flag verbatim.
#
# Usage:
#   ./scripts/update-docker.sh            # same as: bash scripts/update.sh
#   ./scripts/update-docker.sh --no-pull  # forwarded verbatim
#   ./scripts/update-docker.sh -h|--help   # print update.sh's help and exit

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$SCRIPT_DIR/update.sh" ]; then
    echo -e "${RED}Error: scripts/update.sh not found beside this alias — an extracted release bundle ships both.${NC}" >&2
    exit 1
fi

case "${1:-}" in
    -h|--help|"") ;;
    *) echo -e "${YELLOW}update-docker.sh is a compatibility alias — the updater is scripts/update.sh.${NC}" >&2 ;;
esac

exec bash "$SCRIPT_DIR/update.sh" "$@"
