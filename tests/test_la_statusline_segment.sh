#!/usr/bin/env bash
# Tests for bin/la-statusline-segment.sh — local-agents' status-line contribution.
#
# EVERY assertion drives an INJECTED reader (LA_STATUSLINE_RAM_CMD), never this machine's
# real memory. A suite that reads live vm_stat passes or fails by luck, cannot reach the
# near-ceiling branch on a healthy laptop, and would stay green while hiding a regression.
set -uo pipefail
export LC_ALL=C
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEG="$HERE/../bin/la-statusline-segment.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$3" "$2"; }
has() { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "to contain '$3'" "$2" ;; esac; }

run() { # run <base-url> <ram-cmd> [extra env assignments...]
  env ANTHROPIC_BASE_URL="$1" LA_STATUSLINE_RAM_CMD="$2" "${@:3}" bash "$SEG" 2>/dev/null
}

# THE GATE. CLAUDE_IS_LOCAL is set to the value it leaks as: the launcher exports it and it
# survives into a later gateway `claude` from the same shell, so gating on it would report a
# local instrument for a PAID session. The endpoint is the only honest signal.
OUT="$(env -u ANTHROPIC_BASE_URL CLAUDE_IS_LOCAL=true LA_STATUSLINE_RAM_CMD='echo 70.6 128.0' bash "$SEG" 2>/dev/null)"
is "silent on a cloud session even with CLAUDE_IS_LOCAL leaked" "" "$OUT"
is "silent when the endpoint is a remote gateway" "" "$(run 'https://gateway.example.com' 'echo 70.6 128.0')"

# Loopback forms that all mean "local".
has "emits on localhost"  "$(run 'http://localhost:8000' 'echo 70.6 128.0')" '"label":"ram"'
has "emits on 127.0.0.1"  "$(run 'http://127.0.0.1:8000' 'echo 70.6 128.0')" '"label":"ram"'

# THE DENOMINATOR IS THE CAP, NOT INSTALLED RAM. 70.6 of 128 GB installed is 55%; of the
# 103.9 GB ceiling it is 68%. Asserting 68 is what proves the cap is the denominator, and
# asserting 55 is ABSENT is what stops a silent regression to the reassuring number.
OUT="$(run 'http://localhost:8000' 'echo 70.6 128.0')"
has "percentage is of the CAP" "$OUT" '68%'
case "$OUT" in *55%*) bad "installed-RAM percentage must NOT be used" "no 55%" "$OUT" ;; *) ok "installed-RAM percentage must NOT be used" ;; esac
has "text carries both the reading and the ceiling" "$OUT" '"text":"70.6/103.9G 68%"'

# LEVEL THRESHOLDS, each on a planted value so the test can genuinely fail.
has "below warn is ok"    "$(run 'http://localhost:8000' 'echo 50.0 128.0')" '"level":"ok"'
has "at/above warn is warn" "$(run 'http://localhost:8000' 'echo 75.0 128.0')" '"level":"warn"'
has "at/above crit is crit" "$(run 'http://localhost:8000' 'echo 98.5 128.0')" '"level":"crit"'
# Exact boundaries: 70% and 90% of the cap must be inclusive.
has "exactly warn%% is warn" "$(run 'http://localhost:8000' "echo 72.73 103.9")" '"level":"warn"'
has "exactly crit%% is crit" "$(run 'http://localhost:8000' "echo 93.51 103.9")" '"level":"crit"'

# OVERRIDES: the cap and the thresholds are machine/policy specific, not constants.
has "the cap is overridable" "$(run 'http://localhost:8000' 'echo 40.0 64.0' LA_STATUSLINE_CAP_GB=50)" '"text":"40.0/50G 80%"'
has "thresholds are overridable" "$(run 'http://localhost:8000' 'echo 50.0 128.0' LA_STATUSLINE_WARN_PCT=40)" '"level":"warn"'

# SILENCE IS VALID, and must stay exit 0 — a status line can never break a prompt.
is "a failing reader emits nothing" "" "$(run 'http://localhost:8000' 'false')"
run 'http://localhost:8000' 'false' >/dev/null 2>&1
is "…and still exits 0" 0 $?
is "non-numeric reader output is rejected" "" "$(run 'http://localhost:8000' 'echo notanumber alsonot')"
run 'http://localhost:8000' 'echo notanumber alsonot' >/dev/null 2>&1
is "…and that also exits 0" 0 $?

# OUTPUT SHAPE: exactly one line, and valid JSON — the consumer parses it.
is "emits exactly one line" 1 "$(run 'http://localhost:8000' 'echo 70.6 128.0' | wc -l | tr -d ' ')"
if run 'http://localhost:8000' 'echo 70.6 128.0' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert set(d)>={"label","text","level"}' 2>/dev/null
then ok "output is valid JSON with the contract keys"; else bad "output is valid JSON with the contract keys" "parseable JSON" "$(run 'http://localhost:8000' 'echo 70.6 128.0')"; fi

# --help must NOT do the work: probing an unfamiliar script must tell you something, not run it.
HELP="$(bash "$SEG" --help 2>&1)"
has "--help explains the script" "$HELP" "status line"
has "--help documents the contract" "$HELP" "CONTRACT"
case "$HELP" in *'"label":"ram","text":"74.0'*) ok "--help shows the shape, not a live reading" ;; *) bad "--help shows the shape" "the documented example" "$HELP" ;; esac
bash "$SEG" --bogus >/dev/null 2>&1
is "an unrecognised flag exits non-zero" 2 $?

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
