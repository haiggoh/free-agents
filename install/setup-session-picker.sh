#!/usr/bin/env bash
# setup-session-picker.sh — create the pinned venv the session picker UI runs in.
#
# Usage:  setup-session-picker.sh [--check] [--help]
#   (no flag)  create/refresh the venv from install/session-picker-requirements.txt
#   --check    report whether the venv exists and imports textual; change nothing
#
# Environment:
#   LA_PICKER_VENV   venv location (default ~/.local/share/free-agents/picker-venv)
#   LA_PICKER_PYTHON interpreter used to create it (default: uv-managed 3.12, else python3)
#
# The venv is per-user and outside the repo and any plugin cache, so a plugin update never
# deletes it and a checkout never commits it.
set -euo pipefail
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQ="$HERE/session-picker-requirements.txt"
VENV="${LA_PICKER_VENV:-$HOME/.local/share/free-agents/picker-venv}"
CHECK=0
case "${1:-}" in
  -h|--help) sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p;}' "$0"; exit 0 ;;
  --check) CHECK=1 ;;
  "") ;;
  *) echo "setup-session-picker: unknown option: $1 (try --help)" >&2; exit 2 ;;
esac

if [ "$CHECK" -eq 1 ]; then
  if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c 'import textual' 2>/dev/null; then
    echo "session picker venv OK: $VENV (textual $("$VENV/bin/python" -c 'import textual;print(textual.__version__)'))"
    exit 0
  fi
  echo "session picker venv missing or incomplete: $VENV" >&2; exit 1
fi

mkdir -p "$(dirname "$VENV")"
if command -v uv >/dev/null 2>&1; then
  uv venv -q --allow-existing --python "${LA_PICKER_PYTHON:-3.12}" "$VENV"
  uv pip install -q --python "$VENV/bin/python" -r "$REQ"
else
  "${LA_PICKER_PYTHON:-python3}" -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q -r "$REQ"
fi
"$VENV/bin/python" -c 'import textual; print("session picker ready: textual", textual.__version__)'
