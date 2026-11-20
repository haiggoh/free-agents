#!/usr/bin/env bash
# budget-wall-spool.sh — StopFailure hook (INSTANT shell only).
#
# Measured 2026-11-19 (probes q1hook7/8/9/10/11, engine 2.1.327): at StopFailure the engine
# gives the hook only a small, variable window (~100 ms; probes 10/11 showed it winning and
# losing the same race across runs) and kills anything it spawns (same-group backgrounded
# python AND setsid grandchildren both died before their first write). So this hook is
# first in StopFailure and does the minimum: mkdir the pending dir, then consume stdin with
# BASH BUILTINS ONLY (no cat, no date, no subshell — every fork narrows the window), and
# exit. The capture itself runs at the next SessionStart (budget-wall-capture.py --pending),
# where full time is available (Q1/Q2 cat-hook captures prove SessionStart is reliable).
#
# Usage: budget-wall-spool.sh [--help]
# Env: FA_TAKEOVER_DIR (override of ${XDG_STATE_HOME:-~/.local/state}/free-agents/takeovers)
set -u

case "${1:-}" in
    -h|--help)
        echo "budget-wall-spool.sh — StopFailure hook: spool the hook payload to <root>/.pending/<pid>.json"
        echo "Usage: budget-wall-spool.sh [--help]   Env: FA_TAKEOVER_DIR"
        exit 0
        ;;
    "") ;;
    *) echo "budget-wall-spool.sh: unknown flag: $1" >&2; exit 2 ;;
esac

root="${FA_TAKEOVER_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/free-agents/takeovers}"
case "$root" in
    /*) ;;
    *) echo "budget-wall-spool.sh: FA_TAKEOVER_DIR must be absolute: $root" >&2; exit 2 ;;
esac
d="$root/.pending"
# payloads carry the walled session's last message: dir 0700 / file 0600 (umask is a builtin)
umask 077
# NO fork in the hot path when the dir already exists (the SessionStart worker pre-creates
# it): `[ -d ]` is a builtin; mkdir runs only on first use. fail-open on any error.
[ -d "$d" ] || mkdir -p "$d" 2>/dev/null || exit 0
f="$d/$$.json"
{
    # Header line: THIS (dying) session's endpoint. The worker runs in the NEXT session,
    # whose own ANTHROPIC_BASE_URL says nothing about where the wall happened (PR #7
    # review). printf and ${var+x} are builtins/expansions — still zero forks.
    if [ -n "${ANTHROPIC_BASE_URL+x}" ]; then
        printf '#fa-endpoint %s\n' "$ANTHROPIC_BASE_URL"
    else
        printf '#fa-endpoint-unset\n'
    fi
    while IFS= read -r line || [ -n "$line" ]; do
        printf '%s\n' "$line"
    done
} > "$f"
exit 0