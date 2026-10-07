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

# --- OUTCOME, not wiring: run the real launcher and read the prompt it would hand `claude`. ---
# The launcher runs in a sandboxed copy of the runtime with a fixture model, a sandboxed HOME, and
# stubs at the server boundary only (hotswap prints the contract a healthy server would; the RAM
# preflight passes; `claude` prints its --append-system-prompt). Everything between — prompt
# template, placeholder substitution, the shared-rules append — is the real code. It used to run
# against the machine's private roster and a live server, so it failed wherever neither existed.
SB="$(mktemp -d)"; trap 'rm -rf "$SB"' EXIT
. "$HERE/lib/sandbox.sh"
la_test_sandbox "$SB" || { echo "sandbox build failed"; exit 1; }
cp "$ROOT/config/local-agent-system-prompt.txt" "$ROOT/config/shared-agent-shipping-rules.txt" "$SB/config/"
FIXTURE_ALIAS=rules-probe
FIXTURE_PORT=18999
FIXTURE_SPOOF=claude-opus-5
mkdir -p "$SB/home/.models/RulesProbe" "$SB/stub" "$SB/tmp"
head -c 2097152 /dev/zero > "$SB/home/.models/RulesProbe/weights.bin"
cat > "$SB/config/config.local.sh" <<CFG
LA_MODELS_DIR="\$HOME/.models"
la_register $FIXTURE_ALIAS RulesProbe rapid qwen "" false $FIXTURE_SPOOF high
CFG
printf '#!/bin/sh\necho "SUCCESS_PORT=%s"\necho "DISPATCH_MODEL=%s"\n' "$FIXTURE_PORT" "$FIXTURE_SPOOF" > "$SB/bin/local-llm-hotswap.sh"
printf '#!/bin/sh\nexit 0\n' > "$SB/bin/la-ram-preflight.sh"
printf '#!/bin/sh\nexit 7\n' > "$SB/stub/curl"   # no network: /v1/models is unreachable, as offline
printf '#!/bin/sh\nwhile [ $# -gt 0 ]; do if [ "$1" = "--append-system-prompt" ]; then printf "%%s" "$2"; exit 0; fi; shift; done\n' > "$SB/stub/claude"
chmod +x "$SB/bin/local-llm-hotswap.sh" "$SB/bin/la-ram-preflight.sh" "$SB/stub/curl" "$SB/stub/claude"
launch() {  # launch [VAR=value ...] — the assembled prompt (or the launcher's error) on stdout
  env -u LA_SHARED_RULES_FILE -u LA_AUTO_MODE -u LA_BLIND_AUTO HOME="$SB/home" TMPDIR="$SB/tmp" PATH="$SB/stub:$PATH" \
    LA_AUTO_MODE=0 "$@" bash "$SB/bin/launch-claude-agent.sh" "$FIXTURE_ALIAS" 2>&1
}

PROMPT="$(launch)"
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
# Placeholders are substituted from the launch, not left as template tokens.
case "$PROMPT" in
  *__LA_*__*) bad "no template placeholder survives into the prompt" "found __LA_…__" ;;
  *) ok "no template placeholder survives into the prompt" ;;
esac

# NEGATIVE CONTROL: with an EMPTY rules file the probes must vanish. Without this, every
# assertion above would also pass against a launcher that hardcoded the strings.
EMPTY="$(mktemp)"
CTRL="$(launch LA_SHARED_RULES_FILE="$EMPTY")"
rm -f "$EMPTY"
case "$CTRL" in
  *"RUN IT, DON'T JUST READ IT"*) bad "an empty rules file drops the rules" "still present — the probe does not measure the file" ;;
  *) ok "an empty rules file drops the rules (so the checks above are real)" ;;
esac

# A MISSING rules file must be a hard error. Continuing silently is the failure mode that
# makes an un-briefed session indistinguishable from a briefed one.
OUT="$(launch LA_SHARED_RULES_FILE=/nonexistent/shared-rules.txt)"
case "$OUT" in
  *"not readable"*) ok "a missing rules file is reported, not ignored" ;;
  *) bad "a missing rules file is reported, not ignored" "$OUT" ;;
esac

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
