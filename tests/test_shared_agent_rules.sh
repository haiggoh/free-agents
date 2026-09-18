#!/usr/bin/env bash
# The shipping/verification rules must reach BOTH free lanes' system prompts from ONE file.
#
# Why this test exists: the rules were written because free sessions were measured skipping
# these exact steps (shipping untested features, bumping a changelog but not the code, moving
# a published tag). A prompt that silently loses them looks identical to one that has them
# right up until a session ships something broken — so the wiring needs a check that FAILS
# when it breaks, not a reading of the launcher.
set -uo pipefail
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
RULES="$ROOT/config/shared-agent-shipping-rules.txt"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     %s\n' "$1" "${2:-}"; }

usage() {
  cat <<'EOF'
test_shared_agent_rules.sh — assert the shared shipping rules reach both free lanes.

Checks that config/shared-agent-shipping-rules.txt exists, that BOTH launchers read that
same path (so the text cannot drift between local and remote), that the rules actually
appear in the local launcher's assembled --append-system-prompt, that an empty rules file
makes them disappear (proving the check can fail), and that a missing rules file is a
hard error rather than a silent omission.

Usage:
  tests/test_shared_agent_rules.sh          run the checks
  tests/test_shared_agent_rules.sh --help   show this text

Launches no model: `claude` is stubbed on PATH so the prompt is printed, not used.
EOF
}
case "${1:-}" in
  --help|-h) usage; exit 0 ;;
  "") ;;
  *) printf 'test_shared_agent_rules.sh: unknown argument: %s\n' "$1" >&2
     printf 'usage: tests/test_shared_agent_rules.sh [--help]\n' >&2; exit 2 ;;
esac

echo "shared agent shipping rules"

[ -r "$RULES" ] && ok "the shared rules file exists and is readable" \
  || bad "the shared rules file exists and is readable" "$RULES"

# SINGLE SOURCE OF TRUTH. Two prompt files already exist (local + remote); the whole point is
# that the lane-independent rules are not copied into both.
for launcher in bin/launch-claude-agent.sh bin/remote-session.sh; do
  if grep -q 'config/shared-agent-shipping-rules.txt' "$ROOT/$launcher"; then
    ok "$launcher reads the shared rules file"
  else
    bad "$launcher reads the shared rules file" "no reference found"
  fi
done
# Neither lane's own prompt may restate them — that is the drift this prevents.
for f in config/local-agent-system-prompt.txt config/remote-agent-system-prompt.txt; do
  if grep -q "RUN IT, DON'T JUST READ IT" "$ROOT/$f" 2>/dev/null; then
    bad "$f does not duplicate the shared rules" "found a second copy"
  else
    ok "$f does not duplicate the shared rules"
  fi
done

# --- OUTCOME, not wiring: stub `claude` and read the prompt it would have been given. ---
STUB="$(mktemp -d)"; trap 'rm -rf "$STUB"' EXIT
printf '#!/bin/sh\nwhile [ $# -gt 0 ]; do if [ "$1" = "--append-system-prompt" ]; then printf "%%s" "$2"; exit 0; fi; shift; done\n' > "$STUB/claude"
chmod +x "$STUB/claude"
ALIAS="$(bash "$ROOT/bin/launch-claude-agent.sh" --help 2>/dev/null | awk 'NR>2 && NF {print $1; exit}')"
[ -n "$ALIAS" ] || ALIAS="kat-coder-optiq"

PROMPT="$(PATH="$STUB:$PATH" bash "$ROOT/bin/launch-claude-agent.sh" "$ALIAS" 2>/dev/null)"
for probe in "RUN IT, DON'T JUST READ IT" "PLANTED POSITIVE" "FIX FORWARD" "DERIVE, DON'T DUPLICATE" "FEATURE BRANCH, NOT DIRTY ON MAIN" "STAGE BY PATH" "NEVER FORCE-PUSH" "NEVER THE INSTALLED PLUGIN CACHE"; do
  case "$PROMPT" in
    *"$probe"*) ok "the assembled local prompt carries: $probe" ;;
    *) bad "the assembled local prompt carries: $probe" "absent from the prompt" ;;
  esac
done
# The lane-specific text must survive alongside them, not be replaced by them.
case "$PROMPT" in
  *"LOCAL CLAUDE CODE SESSION"*) ok "…and the lane-specific prompt is still present" ;;
  *) bad "…and the lane-specific prompt is still present" "lane text lost" ;;
esac

# NEGATIVE CONTROL: with an EMPTY rules file the probes must vanish. Without this, every
# assertion above would also pass against a launcher that hardcoded the strings.
EMPTY="$(mktemp)"
CTRL="$(PATH="$STUB:$PATH" LA_SHARED_RULES_FILE="$EMPTY" bash "$ROOT/bin/launch-claude-agent.sh" "$ALIAS" 2>/dev/null)"
rm -f "$EMPTY"
case "$CTRL" in
  *"RUN IT, DON'T JUST READ IT"*) bad "an empty rules file drops the rules" "still present — the probe does not measure the file" ;;
  *) ok "an empty rules file drops the rules (so the checks above are real)" ;;
esac

# A MISSING rules file must be a hard error. Continuing silently is the failure mode that
# makes an un-briefed session indistinguishable from a briefed one.
OUT="$(PATH="$STUB:$PATH" LA_SHARED_RULES_FILE=/nonexistent/shared-rules.txt \
       bash "$ROOT/bin/launch-claude-agent.sh" "$ALIAS" 2>&1)"
case "$OUT" in
  *"not readable"*) ok "a missing rules file is reported, not ignored" ;;
  *) bad "a missing rules file is reported, not ignored" "$OUT" ;;
esac

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
