#!/usr/bin/env bash
# check-backend-updates.sh — wrapper for Python update checker
# Called by launchd weekly

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${LA_PYTHON:-python3}"

exec "$PYTHON" "$SCRIPT_DIR/check_backend_updates.py" --save-json "$@"