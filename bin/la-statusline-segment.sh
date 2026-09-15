#!/usr/bin/env bash
# la-statusline-segment.sh — local-agents' contribution to a Claude Code status line.
#
# WHY THIS FILE EXISTS (the layering it fixes): a status-line renderer knows how to lay
# out a line; it has no business knowing what a Metal wired cap is, that ps RSS understates
# MLX pressure by ~7x, or what THIS machine's ceiling happens to be. That knowledge is
# local-agents' domain. So local-agents MEASURES and classifies here, and any renderer just
# places the result. The first version of this feature baked the constant and the memory
# model into the cost-tracker plugin; this is the corrected split.
#
# CONTRACT (stable, so either side can change independently):
#   invoked with NO arguments; prints ONE line of JSON on stdout; exits 0.
#     {"label":"ram","text":"74.0/103.9G 71%","level":"ok"|"warn"|"crit"}
#   `text` is opaque to the consumer — WE choose units, precision and the denominator.
#   `level` carries the thresholds, so the consumer never re-derives them.
#   Prints NOTHING and still exits 0 when there is nothing meaningful to say (not a local
#   session, not macOS, no reading available). Silence is a valid answer; a status line
#   must never be the reason a prompt breaks.
#
# WHAT IT REPORTS, and why not the obvious number: an MLX session dies by OOM against a
# Metal WIRED-memory ceiling well below installed RAM (measured ~103.9 GB on a 128 GB
# M4 Max). Reporting against installed RAM reads reassuring while the session is minutes
# from death, so wired-vs-CAP is the only honest framing. ps RSS is not used at all.
#
# Usage:
#   la-statusline-segment.sh            # emit the JSON segment (or nothing)
#   la-statusline-segment.sh --help
#
# Environment:
#   LA_STATUSLINE_CAP_GB   ceiling to measure against (default 103.9; machine-specific)
#   LA_STATUSLINE_RAM_CMD  override the reader; must print "<wired-gb> <total-gb>".
#                          Exists so tests never depend on real machine memory.
#   LA_STATUSLINE_WARN_PCT / LA_STATUSLINE_CRIT_PCT   thresholds (default 70 / 90)
set -uo pipefail

# Arguments are parsed BEFORE any work: probing an unfamiliar script with --help must
# never make it do its job and print something that merely looks like help.
case "${1:-}" in
  -h|--help)
    sed -n '2,/^set -uo pipefail/{ /^set -uo pipefail/d; s/^# \{0,1\}//; p; }' "$0"
    exit 0 ;;
  "") : ;;
  *)
    printf 'la-statusline-segment.sh: unrecognised argument: %s\n' "$1" >&2
    printf "Try 'la-statusline-segment.sh --help'.\n" >&2
    exit 2 ;;
esac

# GATE ON THE ENDPOINT, NEVER ON CLAUDE_IS_LOCAL. That flag is exported by the local
# launcher and LEAKS into a later gateway `claude` from the same shell, which would report
# a local instrument for a PAID session. ANTHROPIC_BASE_URL is per-process and honest.
case "${ANTHROPIC_BASE_URL:-}" in
  *localhost*|*127.0.0.1*|*'[::1]'*) ;;
  *) exit 0 ;;
esac

RAW=""
if [ -n "${LA_STATUSLINE_RAM_CMD:-}" ]; then
    RAW=$($LA_STATUSLINE_RAM_CMD 2>/dev/null) || RAW=""
elif [ "$(uname -s 2>/dev/null)" = "Darwin" ] && command -v vm_stat >/dev/null 2>&1; then
    # Same derivation as la-ram-preflight.sh (wired pages x page size), deliberately not
    # re-invented: two different numbers for "wired" in one toolchain is worse than none.
    # Page size comes from vm_stat's own header rather than being assumed to be 16k.
    RAW=$(vm_stat 2>/dev/null | awk -v tot="$(sysctl -n hw.memsize 2>/dev/null)" '
      /page size of/{gsub(/[^0-9]/,"",$0); ps=$0}
      /Pages wired down/{gsub(/[^0-9]/,"",$4); w=$4}
      END{ if (ps>0 && w>0 && tot>0) printf "%.1f %.1f", w*ps/1073741824, tot/1073741824 }')
fi
[ -n "$RAW" ] || exit 0

read -r WIRED TOTAL <<EOF2
$RAW
EOF2
# Reject anything non-numeric rather than passing garbage into the renderer.
case "${WIRED:-}" in ''|*[!0-9.,]*) exit 0 ;; esac
WIRED=${WIRED//,/.}

CAP="${LA_STATUSLINE_CAP_GB:-103.9}"
case "$CAP" in ''|*[!0-9.,]*) CAP=103.9 ;; esac
CAP=${CAP//,/.}
WARN="${LA_STATUSLINE_WARN_PCT:-70}"
CRIT="${LA_STATUSLINE_CRIT_PCT:-90}"

PCT=$(LC_ALL=C awk -v w="$WIRED" -v c="$CAP" 'BEGIN{ if (c+0>0) printf "%.0f", (w/c)*100 }')
[ -n "$PCT" ] || exit 0

LEVEL=ok
if   [ "$PCT" -ge "$CRIT" ] 2>/dev/null; then LEVEL=crit
elif [ "$PCT" -ge "$WARN" ] 2>/dev/null; then LEVEL=warn
fi

# Hand-built JSON is fine for three known-shaped scalar fields: every value is numeric or
# one of three literals, so there is nothing here that could need escaping.
printf '{"label":"ram","text":"%s/%sG %s%%","level":"%s"}\n' "$WIRED" "$CAP" "$PCT" "$LEVEL"
