#!/usr/bin/env bash
# setup-gui.sh — create the pinned venv the OPTIONAL native GUI (bin/free-agents-gui) runs in.
# Usage:  setup-gui.sh [--check] [--dev] [--help]
#   (no flag)  create/refresh the venv from install/gui-requirements.txt
#   --check    report whether the venv exists and imports nicegui + webview; change nothing
#   --dev      also install the TEST-only deps (install/gui-dev-requirements.txt: Playwright,
#              pytest) and Playwright's chromium + webkit browsers
# Environment:
#   LA_GUI_VENV    venv location (default ~/.local/share/free-agents/gui-venv)
#   LA_GUI_PYTHON  interpreter used to create it (default: uv-managed 3.12, else python3)
# The venv is per-user and outside the repo and any plugin cache, so a plugin update never
# deletes it. It is separate from the session-picker venv: the Textual TUI and the direct CLI
# never need NiceGUI, pywebview or PyObjC. Removing it removes only the GUI.
set -euo pipefail
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQ="$HERE/gui-requirements.txt"
VENV="${LA_GUI_VENV:-$HOME/.local/share/free-agents/gui-venv}"
CHECK=0
DEV=0
usage() { sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p;}' "$0"; }
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --check) CHECK=1 ;;
  --dev) DEV=1 ;;
  "") ;;
  *) echo "setup-gui: unknown option: $1" >&2; usage >&2; exit 2 ;;
esac
probe='import nicegui, webview; print(nicegui.__version__)'
if [ "$CHECK" -eq 1 ]; then
  if [ -x "$VENV/bin/python" ] && v="$("$VENV/bin/python" -c "$probe" 2>/dev/null)"; then
    echo "gui venv OK: $VENV (nicegui $v)"
    exit 0
  fi
  echo "gui venv missing or incomplete: $VENV — run $HERE/setup-gui.sh" >&2; exit 1
fi
mkdir -p "$(dirname "$VENV")"
if command -v uv >/dev/null 2>&1; then
  uv venv -q --allow-existing --python "${LA_GUI_PYTHON:-3.12}" "$VENV"
  uv pip install -q --python "$VENV/bin/python" -r "$REQ"
else
  "${LA_GUI_PYTHON:-python3}" -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q -r "$REQ"
fi
if [ "$DEV" -eq 1 ]; then
  if command -v uv >/dev/null 2>&1; then
    uv pip install -q --python "$VENV/bin/python" -r "$HERE/gui-dev-requirements.txt"
  else
    "$VENV/bin/python" -m pip install -q -r "$HERE/gui-dev-requirements.txt"
  fi
  "$VENV/bin/python" -m playwright install chromium webkit
fi
# Same Homebrew-keg durability fix as the picker venv (install/managers/durability.py).
python3 "$HERE/managers/durability.py" normalize "$VENV" >/dev/null \
  || echo "⚠️  $VENV is built on a Homebrew patch keg; it will break at the next Python patch upgrade — re-run this script then." >&2
echo "gui ready: nicegui $("$VENV/bin/python" -c "$probe")"
