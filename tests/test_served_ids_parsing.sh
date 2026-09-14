#!/usr/bin/env bash
# test_served_ids_parsing.sh — parsing the ids a port advertises must survive whitespace AND
# return EVERY id, not just one.
#
# WHY this exists: four scripts decide what a server is serving by parsing /v1/models, and every one
# of them used `grep -o '"id":"[^"]*"'` — which silently returns nothing if the server pretty-prints
# ("id": "x"). Measured 2026-09-13: la-reboot reported a working server as "not ready" for exactly
# this reason. The scripts survive today only because Rapid emits compact JSON; that is luck, not
# design, and the failure is silent-empty rather than loud.
#
# The SECOND trap, which the first fix walked into: `sed -n 's/.*"id"..."\(...\)".*/\1/p'` is
# whitespace-tolerant but returns only the LAST id on a line, because the leading `.*` is greedy.
# vllm-mlx serves EVERY id in the spoof list, and hotswap/la-ram-preflight compare against the whole
# set, so losing all but one would break reuse matching. Both properties are required together.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

pass=0 fail=0
ok()  { printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  ✗ %s\n' "$1"; fail=$((fail+1)); }

# shellcheck source=/dev/null
. config/config-lib.sh

echo "la_parse_served_ids:"

if ! command -v la_parse_served_ids >/dev/null 2>&1; then
  bad "config-lib.sh does not define la_parse_served_ids"
  printf '\n%d passed, %d failed\n' "$pass" "$fail"; exit 1
fi
ok "config-lib.sh defines la_parse_served_ids"

COMPACT='{"data":[{"id":"claude-opus-5"},{"id":"claude-opus-4-8"},{"id":"qwen-3.8-operator"}]}'
PRETTY='{"data": [ { "id" : "claude-opus-5" }, { "id": "claude-opus-4-8" } ]}'

got="$(printf '%s' "$COMPACT" | la_parse_served_ids | tr '\n' ' ')"
if [ "$got" = "claude-opus-5 claude-opus-4-8 qwen-3.8-operator " ]; then
  ok "compact JSON yields ALL three ids in order"
else
  bad "compact JSON gave '$got'"
fi

got="$(printf '%s' "$PRETTY" | la_parse_served_ids | tr '\n' ' ')"
if [ "$got" = "claude-opus-5 claude-opus-4-8 " ]; then
  ok "pretty-printed JSON yields both ids (the original bug: yielded NOTHING)"
else
  bad "pretty JSON gave '$got'"
fi

# One id per line is what every caller assumes (they use grep -qxF against it).
n="$(printf '%s' "$COMPACT" | la_parse_served_ids | wc -l | tr -d ' ')"
[ "$n" = 3 ] && ok "emits one id per line" || bad "emitted $n line(s) for 3 ids"

# An mlx_lm tier serves the model DIRECTORY as its id — slashes, dots and dashes must survive.
got="$(printf '%s' '{"data":[{"id":"/Users/x/.models/Llama-4-Scout-17B-16E-Instruct-4bit"}]}' | la_parse_served_ids)"
[ "$got" = "/Users/x/.models/Llama-4-Scout-17B-16E-Instruct-4bit" ] \
  && ok "a path-shaped id survives intact" || bad "path id mangled: '$got'"

# Must not invent ids from an error page, and must not match neighbouring keys.
got="$(printf '%s' '{"detail":"The model does not exist"}' | la_parse_served_ids | wc -l | tr -d ' ')"
[ "$got" = 0 ] && ok "an error payload yields no ids" || bad "error payload produced $got id(s)"
got="$(printf '%s' '{"model_id":"nope","id":"yes"}' | la_parse_served_ids | tr '\n' ' ')"
[ "$got" = "yes " ] && ok "only the 'id' key matches, not 'model_id'" || bad "key matching wrong: '$got'"
got="$(printf '' | la_parse_served_ids | wc -l | tr -d ' ')"
[ "$got" = 0 ] && ok "empty input yields nothing (a dead port)" || bad "empty input produced $got line(s)"

echo
echo "no caller keeps the brittle pattern:"
for f in bin/local-llm-hotswap.sh bin/la-ram-preflight.sh bin/launch-claude-agent.sh bin/la-reboot.sh; do
  if grep -q "grep -o '\"id\":\"" "$f"; then
    bad "$(basename "$f") still uses the whitespace-intolerant pattern"
  else
    ok "$(basename "$f") no longer uses the brittle pattern"
  fi
done
# la-reboot is deliberately standalone (it must run when the stack is sick), so it carries its own
# copy rather than sourcing config-lib — but it must still handle multiple ids.
if grep -qE 'grep -oE .\"id\"' bin/la-reboot.sh; then
  ok "la-reboot.sh carries a standalone whitespace-tolerant, multi-id parser"
else
  bad "la-reboot.sh lacks its own correct parser"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
