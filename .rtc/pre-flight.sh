#!/usr/bin/env bash
# .rtc/pre-flight.sh — free-agents' extra test gates, before anything else is shipped.
#
# Extracted in behaviour from the old ~/.claude/scripts/fa-ship.sh, which ran the main suite and the
# two venv suites TOGETHER. rtc-ship.sh runs the main suite itself, so this hook owns the two that
# need their own interpreters:
#
#   LiteLLM contract — test_la_proxy_hooks.py, test_anthropic_route.py, test_litellm_contract.py
#   picker pilots    — test_session_picker_pty.py, test_new_features.py
#
# The main suite alone SKIPS all five files, so without this hook a release ships untested code in
# the proxy contract and the TUI. That is the whole reason these gates exist.
#
# ONE DELIBERATE CHANGE from fa-ship.sh: a MISSING venv now FAILS the gate. It used to print
# "⚠ no LiteLLM venv — the contract gate is NOT run" and succeed, which is a gate that reports
# success while covering nothing — the exact failure shape rtc-ship.sh exists to prevent. The venvs
# are created by this repo's own scripts, so a missing one is a broken environment, not a reason to
# ship blind. Set FA_ALLOW_MISSING_VENVS=1 to keep the old warning-and-continue behaviour.
#
# Env: FA_LITELLM_PY / FA_PICKER_PY   interpreters for the two gates (defaults below)
#      FA_ALLOW_MISSING_VENVS=1        degrade to the old warning instead of failing
# Usage: ./.rtc/pre-flight.sh   (run by rtc-ship.sh from the repo root)
set -uo pipefail

LITELLM_PY=${FA_LITELLM_PY:-$HOME/.local/pipx/venvs/litellm/bin/python}
PICKER_PY=${FA_PICKER_PY:-$HOME/.local/share/free-agents/picker-venv/bin/python}

LITELLM_TESTS="tests/test_la_proxy_hooks.py tests/test_anthropic_route.py tests/test_litellm_contract.py"
PICKER_TESTS="tests/test_session_picker_pty.py tests/test_new_features.py"

# Only what EXISTS: a renamed or deleted test file must not reach pytest as a path error, and a
# gate whose files are ALL gone must fail rather than "pass" over nothing.
existing() {  # existing <space-separated files> -> the subset that exists, one per line
  local f
  for f in $1; do [ -f "$f" ] && printf '%s\n' "$f"; done
  return 0
}

run_gate() {  # run_gate <name> <interpreter> <space-separated test files>
  local name=$1 py=$2 files=$3 d rc
  local -a t=()
  while IFS= read -r f; do [ -n "$f" ] && t+=("$f"); done < <(existing "$files")
  if [ ! -x "$py" ]; then
    if [ "${FA_ALLOW_MISSING_VENVS:-0}" = 1 ]; then
      echo "⚠ no $name venv at $py — the $name gate is NOT run" >&2; return 0
    fi
    echo "✖ no $name venv at $py — the $name gate would NOT run" >&2
    echo "  recreate it, or set FA_ALLOW_MISSING_VENVS=1 to knowingly ship without it" >&2
    return 1
  fi
  if [ "${#t[@]}" -eq 0 ]; then
    echo "✖ $name gate: none of its test files exist — the gate would cover nothing" >&2; return 1
  fi
  d=$(mktemp -d)
  if "$py" -m pytest -q "${t[@]}" -p no:cacheprovider </dev/null >"$d/out" 2>&1; then
    echo "  $name: $(grep -E 'passed|failed|no tests ran' "$d/out" | tail -1)"
    rm -rf "$d"; return 0
  fi
  echo "✖ $name gate FAILED: $(grep -E 'passed|failed|error' "$d/out" | tail -1)" >&2
  grep -E '^(FAILED|ERROR) ' "$d/out" | head -10 >&2
  echo "  full log kept in $d" >&2
  return 1
}

# Both gates in parallel: independent interpreters, and the gate is dominated by interpreter startup.
a=$(mktemp -d); b=$(mktemp -d)
( run_gate litellm "$LITELLM_PY" "$LITELLM_TESTS" >"$a/out" 2>&1; echo $? >"$a/rc" ) &
pa=$!
( run_gate picker  "$PICKER_PY"  "$PICKER_TESTS"  >"$b/out" 2>&1; echo $? >"$b/rc" ) &
pb=$!
wait "$pa" "$pb"
cat "$a/out" "$b/out"
rc=0
[ "$(cat "$a/rc" 2>/dev/null || echo 1)" = 0 ] || rc=1
[ "$(cat "$b/rc" 2>/dev/null || echo 1)" = 0 ] || rc=1
rm -rf "$a" "$b"
[ "$rc" = 0 ] || echo "✖ pre-flight gates FAILED" >&2
exit "$rc"
