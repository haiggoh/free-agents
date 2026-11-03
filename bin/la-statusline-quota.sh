#!/usr/bin/env bash
# la-statusline-quota.sh — quota segment for the Claude Code status line: today's gateway
# spend out of the daily cap, read from the cost-tracker ledger.
#
# WHY A SEPARATE SCRIPT: la-statusline-segment.sh owns the RAM/Metal instrument (local
# sessions); the quota owns the LEDGER (cloud/gateway spend). One script, one JSON line —
# the renderer captures exactly one line per segment script.
#
# CONTRACT (same shape as the RAM segment):
#   invoked with NO arguments; prints ONE line of JSON on stdout; exits 0.
#     {"label":"quota","text":"today $1.23/$40","level":"ok"|"warn"|"crit"}
#   Prints NOTHING and still exits 0 when the ledger is missing, unreadable, or holds NO
#   real line — an empty/corrupt ledger must show NO segment, never an error or a $0/$40.
#
# LEDGER FORMAT (owned by cost-tracker, bin/cost-ledger-capture.sh — the writer is the
# source of truth, read it before changing this reader):
#   <ledger_dir>/<session_id>, each line: "<utc_date> <cum_cost_usd> <baseline> [<phantom> <phantom_base>]"
#   A line's spend attributable to <utc_date> is (cum - baseline). Legacy 2-field lines
#   ("<date> <cum>") are still read with baseline 0. Lines that do not parse are skipped
#   and do NOT count as real.
#
# Usage:
#   la-statusline-quota.sh            # emit the JSON segment (or nothing)
#   la-statusline-quota.sh --help
#
# Environment:
#   LA_STATUSLINE_LEDGER_DIR   ledger dir (default ~/.claude/cost-ledger; cost-tracker's
#                              COST_TRACKER_LEDGER_DIR is honoured as a fallback)
#   LA_STATUSLINE_CAP_USD      daily cap in USD (default 40)
#   LA_STATUSLINE_WARN_PCT / LA_STATUSLINE_CRIT_PCT   thresholds (default 80 / 95)
set -uo pipefail

# Arguments are parsed BEFORE any work: probing an unfamiliar script with --help must
# never make it do its job and print something that merely looks like help.
case "${1:-}" in
  -h|--help)
    sed -n '2,/^set -uo pipefail/{ /^set -uo pipefail/d; s/^# \{0,1\}//; p; }' "$0"
    exit 0 ;;
  "") : ;;
  *)
    printf 'la-statusline-quota.sh: unrecognised argument: %s\n' "$1" >&2
    printf "Try 'la-statusline-quota.sh --help'.\n" >&2
    exit 2 ;;
esac

LEDGER_DIR="${LA_STATUSLINE_LEDGER_DIR:-${COST_TRACKER_LEDGER_DIR:-$HOME/.claude/cost-ledger}}"
CAP="${LA_STATUSLINE_CAP_USD:-40}"
case "$CAP" in ''|*[!0-9.,]*) CAP=40 ;; esac
CAP=${CAP//,/.}
WARN="${LA_STATUSLINE_WARN_PCT:-80}"
CRIT="${LA_STATUSLINE_CRIT_PCT:-95}"

# No ledger dir at all -> no segment, no error.
[ -d "$LEDGER_DIR" ] || exit 0

shopt -s nullglob
files=("$LEDGER_DIR"/*)
[ "${#files[@]}" -gt 0 ] || exit 0

TODAY="$(date -u +%F)"

# One awk pass over every ledger file: count real lines and sum today's (cum - baseline).
# Unparseable lines are skipped and do not count as real.
OUT=$(LC_ALL=C awk -v today="$TODAY" '
  {
    real = 0
    if (NF >= 3 && $1 ~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/ \
        && $2 ~ /^[0-9]+(\.[0-9]+)?$/ && $3 ~ /^[0-9]+(\.[0-9]+)?$/) {
      real = 1; base = $3
    } else if (NF >= 2 && $1 ~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/ \
               && $2 ~ /^[0-9]+(\.[0-9]+)?$/) {
      real = 1; base = 0        # legacy 2-field line: baseline defaults to 0 (self-heals)
    }
    if (!real) next
    n++
    if ($1 == today) spend += ($2 - base)
  }
  END { printf "%d %.4f\n", n + 0, spend + 0 }
' "${files[@]}" 2>/dev/null) || OUT="0 0"

read -r REAL SPEND <<EOF2
$OUT
EOF2
case "${REAL:-0}" in ''|*[!0-9]*) REAL=0 ;; esac
# The gate: at least one REAL ledger line, otherwise the segment is absent entirely.
[ "$REAL" -ge 1 ] || exit 0

SPEND_FMT=$(LC_ALL=C awk -v s="$SPEND" 'BEGIN { printf "%.2f", s }')
PCT=$(LC_ALL=C awk -v s="$SPEND" -v c="$CAP" 'BEGIN { if (c + 0 > 0) printf "%.0f", (s / c) * 100 }')
[ -n "$PCT" ] || PCT=0

LEVEL=ok
if   [ "$PCT" -ge "$CRIT" ] 2>/dev/null; then LEVEL=crit
elif [ "$PCT" -ge "$WARN" ] 2>/dev/null; then LEVEL=warn
fi

# Hand-built JSON is fine for known-shaped scalars (numeric or one of three literals):
# nothing here can need escaping.
printf '{"label":"quota","text":"today $%s/$%s","level":"%s"}\n' "$SPEND_FMT" "$CAP" "$LEVEL"
