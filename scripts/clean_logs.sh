#!/bin/bash

# Health Assistant Log Cleanup Script
#
# Remove the dev-time log files (``logging/*.log``, the root ``*.log``,
# the ``backend/*.log`` stray files, and the ``backend/flake8_report.txt``
# / ``backend/mypy_report.txt`` reports). Does not touch container logs
# (those live inside Docker volumes — use ``docker compose logs`` instead).
#
# Usage:
#   ./scripts/clean_logs.sh                 # remove dev log files
#   ./scripts/clean_logs.sh -h | --help     # print this help and exit
#
# Run from anywhere — resolves the project root from this script's path.
# Safe even if no logs exist (``rm -f`` is silent on missing files).

print_help() {
  sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -h|--help) print_help ;;
        *)
            echo "Unknown parameter: $1 (try --help)" >&2
            exit 1
            ;;
    esac
done

# Resolve the absolute path of the project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logging"

echo "Cleaning logs in $LOG_DIR..."
if [ -d "$LOG_DIR" ]; then
    rm -f "$LOG_DIR"/*.log
    echo "Done cleaning $LOG_DIR"
else
    echo "Directory $LOG_DIR does not exist."
fi

echo "Cleaning other log files in root..."
rm -f "$ROOT_DIR"/*.log
rm -f "$ROOT_DIR"/backend/*.log
rm -f "$ROOT_DIR"/backend/flake8_report.txt
rm -f "$ROOT_DIR"/backend/mypy_report.txt

echo "Log cleanup complete."
