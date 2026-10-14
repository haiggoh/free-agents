#!/usr/bin/env bash
# la-mod-attach.sh — print the --plugin-dir path of the session-identity mod, or nothing.
#
# Usage:
#   la-mod-attach.sh            print the mod folder (absolute) when it should be attached
#   la-mod-attach.sh --help     this text
#
# Prints the folder ONLY when every condition holds, and exits 0 either way (nothing printed
# means "do not attach"), so a launcher can always write:
#   _mod=$(la-mod-attach.sh) && [ -n "$_mod" ] && args+=(--plugin-dir "$_mod")
#   1. LA_SESSION_KIND is "local" or "free_api" -- gateway sessions never get the mod;
#   2. the installed `claude` is 2.1.296 or newer (the first build with the mods API this
#      mod was written and tested against);
#   3. the mod folder exists beside this script (../mods/session-identity).
#
# Environment:
#   LA_SESSION_KIND        local | free_api | anything else (= do not attach)
#   LA_MOD_DISABLE=1       never attach (the escape hatch)
#   LA_MOD_CLAUDE_VERSION  use this version string instead of running `claude --version`
#                          (for tests; e.g. "2.1.250 (Claude Code)")
#   LA_MOD_MIN_VERSION     minimum version, default 2.1.296
set -uo pipefail

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set -uo pipefail/{ /^set -uo pipefail/d; s/^# \{0,1\}//; p; }' "$0"
    exit 0 ;;
  "") : ;;
  *)
    printf 'la-mod-attach.sh: unrecognised argument: %s\n' "$1" >&2
    printf "Try 'la-mod-attach.sh --help'.\n" >&2
    exit 2 ;;
esac

[ "${LA_MOD_DISABLE:-0}" = "1" ] && exit 0
case "${LA_SESSION_KIND:-}" in local|free_api) ;; *) exit 0 ;; esac

_here="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_mod="$(cd -P "$_here/../mods/session-identity" 2>/dev/null && pwd)" || exit 0
[ -f "$_mod/.claude-plugin/plugin.json" ] || exit 0

_ver="${LA_MOD_CLAUDE_VERSION:-$(claude --version 2>/dev/null)}"
_ver="${_ver%% *}"
# Plain numeric compare, field by field: bash 3.2 and BSD sort have no `sort -V`.
if LC_ALL=C awk -v have="$_ver" -v want="${LA_MOD_MIN_VERSION:-2.1.296}" 'BEGIN{
    n = split(have, h, "."); split(want, w, ".")
    if (n < 3 || have !~ /^[0-9]+\.[0-9]+\.[0-9]+$/) exit 1
    for (i = 1; i <= 3; i++) { if (h[i]+0 > w[i]+0) exit 0; if (h[i]+0 < w[i]+0) exit 1 }
    exit 0 }'; then
  printf '%s\n' "$_mod"
fi
exit 0
