#!/bin/sh
# locate.sh — the thin launcher's contract: find the installed Free Agents, check the GUI venv,
# start the native GUI detached, and report readiness or a startup failure.
#
# Usage: locate.sh [--launch | --check | --print-root | --help]
#   --launch      (default) start bin/free-agents-gui --native detached; exit 0 once the window
#                 has loaded the UI (or is still starting after 30 s); a crash before that exits
#                 non-zero with the log tail on stderr
#   --check       resolve the plugin + GUI venv and report; start nothing
#   --print-root  print the resolved plugin dir and exit
#
# Shared by the macOS applet (Free Agents.app runs it; a non-zero exit becomes a native dialog
# showing stderr) and the Linux Desktop Entry (Exec= runs it; errors go to zenity/kdialog/
# notify-send when present, and always to the log). It runs from Finder / the desktop with a
# MINIMAL environment, so it never relies on PATH, the cwd or shell profiles.
#
# Resolution (same order as bin/free-agents-resolve.sh), first root containing bin/free-agents-gui:
#   1. FREE_AGENTS_PLUGIN from the config file (must be a plugin dir with the GUI, else exit 2)
#   2. highest version in ${PLUGIN_CACHE:-~/.claude/plugins/cache}/haiggoh/free-agents/
#      (versions predating the GUI are skipped)
#   3. PLUGIN_HINT from the config file (the root that generated the launcher)
#
# Config: ${XDG_CONFIG_HOME:-~/.config}/free-agents/gui-launcher.conf, KEY='value' lines written by
#   install/gui-launcher.py (parsed, never sourced): FREE_AGENTS_PLUGIN, PLUGIN_CACHE, PLUGIN_HINT,
#   GUI_VENV (default ~/.local/share/free-agents/gui-venv).
# Log: ~/Library/Logs/free-agents/gui.log (macOS) or ${XDG_STATE_HOME:-~/.local/state}/free-agents/
#   gui.log (Linux); rotated to gui.log.1 above 1 MB.
# Exit codes: 0 ok, 2 bad config/usage, 3 GUI venv missing, 4 Free Agents not found, 5 GUI crashed.
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
set -u

usage() { sed -n '2,/^PATH=/{/^PATH=/d;s/^# \{0,1\}//;p;}' "$0"; }

MODE=launch
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --launch|"") MODE=launch ;;
  --check) MODE=check ;;
  --print-root) MODE=root ;;
  *) echo "locate.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
esac
[ $# -le 1 ] || { echo "locate.sh: one option at most" >&2; usage >&2; exit 2; }

OS=$(uname -s)
if [ "$OS" = Darwin ]; then
  LOG_DIR="$HOME/Library/Logs/free-agents"
else
  LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/free-agents"
fi
LOG="$LOG_DIR/gui.log"

# present_error <exit code> <message>: the only per-OS part of the contract.
present_error() {
  code=$1; shift
  printf '%s\n' "$*" >&2
  if [ "$OS" != Darwin ] && [ ! -t 2 ]; then     # macOS: the applet turns stderr into a dialog
    if command -v zenity >/dev/null 2>&1; then zenity --error --title "Free Agents" --text "$*" 2>/dev/null
    elif command -v kdialog >/dev/null 2>&1; then kdialog --title "Free Agents" --error "$*" 2>/dev/null
    elif command -v notify-send >/dev/null 2>&1; then notify-send "Free Agents" "$*" 2>/dev/null
    fi
  fi
  if mkdir -p "$LOG_DIR" 2>/dev/null; then
    printf '%s locate.sh error %s: %s\n' "$(date '+%F %T')" "$code" "$*" >>"$LOG" 2>/dev/null
  fi
  exit "$code"
}

CONF="${XDG_CONFIG_HOME:-$HOME/.config}/free-agents/gui-launcher.conf"
conf_get() {   # conf_get KEY -> value of the last KEY='...' line, or empty
  [ -f "$CONF" ] || return 0
  sed -n "s/^$1='\(.*\)'\$/\1/p" "$CONF" | tail -n 1
}
FREE_AGENTS_PLUGIN=$(conf_get FREE_AGENTS_PLUGIN)
PLUGIN_CACHE=$(conf_get PLUGIN_CACHE)
PLUGIN_HINT=$(conf_get PLUGIN_HINT)
GUI_VENV=$(conf_get GUI_VENV)
[ -n "$GUI_VENV" ] || GUI_VENV="$HOME/.local/share/free-agents/gui-venv"

is_plugin() { [ -f "$1/.claude-plugin/plugin.json" ]; }
has_gui() { is_plugin "$1" && [ -x "$1/bin/free-agents-gui" ]; }

resolve_root() {
  if [ -n "$FREE_AGENTS_PLUGIN" ]; then
    is_plugin "$FREE_AGENTS_PLUGIN" || present_error 2 \
      "FREE_AGENTS_PLUGIN=$FREE_AGENTS_PLUGIN (in $CONF) is not a Free Agents plugin dir."
    has_gui "$FREE_AGENTS_PLUGIN" || present_error 2 \
      "FREE_AGENTS_PLUGIN=$FREE_AGENTS_PLUGIN (in $CONF) has no GUI (bin/free-agents-gui) — update that checkout."
    printf '%s\n' "$FREE_AGENTS_PLUGIN"; return 0
  fi
  cache="${PLUGIN_CACHE:-$HOME/.claude/plugins/cache}/haiggoh/free-agents"
  if [ -d "$cache" ]; then
    # Numeric version order, newest first (0.10.0 beats 0.9.0).
    for v in $(for d in "$cache"/*/; do n=${d%/}; n=${n##*/}
                 # patterns parenthesised: bash 3.2 (macOS /bin/sh) misparses ')' inside $( )
                 case $n in (""|*[!0-9.]*) ;; (*) printf '%s\n' "$n" ;; esac
               done | sort -t. -k1,1nr -k2,2nr -k3,3nr -k4,4nr); do
      if has_gui "$cache/$v"; then printf '%s\n' "$cache/$v"; return 0; fi
    done
  fi
  if [ -n "$PLUGIN_HINT" ] && has_gui "$PLUGIN_HINT"; then
    printf '%s\n' "$PLUGIN_HINT"; return 0
  fi
  present_error 4 "Free Agents is not installed: no copy with the GUI was found in $cache${PLUGIN_HINT:+ or $PLUGIN_HINT}. Install or update the free-agents plugin, then run its install/setup-gui.sh."
}

ROOT=$(resolve_root) || exit $?
if [ "$MODE" = root ]; then printf '%s\n' "$ROOT"; exit 0; fi

if [ ! -x "$GUI_VENV/bin/python" ] || ! "$GUI_VENV/bin/python" -c 'import nicegui, webview' >/dev/null 2>&1; then
  present_error 3 "The Free Agents GUI is not set up yet (no working GUI environment at $GUI_VENV). Run once in a terminal: $ROOT/install/setup-gui.sh"
fi
if [ "$MODE" = check ]; then
  echo "plugin: $ROOT"
  echo "gui venv: $GUI_VENV"
  exit 0
fi

mkdir -p "$LOG_DIR" || present_error 2 "cannot create the log directory $LOG_DIR"
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 1048576 ]; then mv -f "$LOG" "$LOG.1"; fi
start=$( [ -f "$LOG" ] && wc -c <"$LOG" | tr -d ' ' || echo 0)
printf '%s locate.sh: starting %s/bin/free-agents-gui --native\n' "$(date '+%F %T')" "$ROOT" >>"$LOG"
start=$((start + 0))
READY="${TMPDIR:-/tmp}/fa-gui-ready.$$"
rm -f "$READY"
# ALL THREE streams detached: do shell script / the desktop wait until stdout closes.
LA_GUI_VENV="$GUI_VENV" LA_GUI_READY_FILE="$READY" \
  nohup "$ROOT/bin/free-agents-gui" --native </dev/null >>"$LOG" 2>&1 &
pid=$!
i=0
while [ "$i" -lt 60 ]; do
  if [ -e "$READY" ]; then rm -f "$READY"; exit 0; fi
  if ! kill -0 "$pid" 2>/dev/null; then
    [ -e "$READY" ] && { rm -f "$READY"; exit 0; }
    tailtxt=$(tail -c +"$((start + 1))" "$LOG" | tail -n 15)
    present_error 5 "The Free Agents GUI stopped while starting. Last log lines ($LOG):
$tailtxt"
  fi
  sleep 0.5
  i=$((i + 1))
done
printf '%s locate.sh: still starting after 30 s (pid %s); not an error yet\n' "$(date '+%F %T')" "$pid" >>"$LOG"
exit 0
