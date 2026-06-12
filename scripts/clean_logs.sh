#!/bin/bash

# Health Assistant Log Cleanup Script

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
