#!/usr/bin/env bash
# check-backend-updates.sh — wrapper for Python update checker
# Called by launchd weekly
# Usage: check-backend-updates.sh [--help] [checker options...]   (options pass through to check_backend_updates.py)
# Environment: LA_PYTHON  interpreter to run the checker with (default python3)
case "${1:-}" in
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${LA_PYTHON:-python3}"

exec "$PYTHON" "$SCRIPT_DIR/check_backend_updates.py" --save-json "$@"