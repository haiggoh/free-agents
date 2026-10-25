#!/usr/bin/env bash
# .rtc/post-release.sh — after the release: trigger the Homebrew tap, then VERIFY the refresh landed.
#
# Extracted from the old ~/.claude/scripts/fa-ship.sh. rtc-ship.sh calls this after it has already
# run `get-haiggoh apply --only free-agents`, and its exit status now FAILS the ship (0.6.6) — so
# this hook is where the outcome is confirmed rather than assumed.
#
# The two halves are deliberately asymmetric:
#
#   the installed copy  — FATAL. If it is not at $V the refresh silently did nothing, and the user
#                         is still running the old plugin while the release says otherwise.
#   the Homebrew formula — a WARNING. haiggoh/homebrew-tap's bump-formulas workflow also runs
#                         HOURLY on its own, so a slow tap self-heals; failing a completed release
#                         over it would block the ship for something that fixes itself.
#
# Everything prints to STDERR: rtc-ship.sh discards the hook's stdout, so a result only the
# operator can act on must not be written where it is thrown away.
#
# Env: FA_SKIP_BREW=1    do not trigger bump-formulas.yml
#      FA_BREW_WAIT      seconds to wait for the tap formula (default 120; it lands in ~15 s)
# Usage: ./.rtc/post-release.sh   (run by rtc-ship.sh from the repo root, stdout discarded)
set -uo pipefail

say() { printf '%s\n' "$*" >&2; }

V=$(tr -d '[:space:]' < VERSION)

# --- the installed copy: FATAL --------------------------------------------------------------
# Versionless numeric sort over the cache dirs: they are named by version and there is no index to
# read. Read the manifest rather than trusting the directory name.
installed=""
for base in "${RTC_PLUGIN_CACHE:-$HOME/.claude/plugins/cache}"/*/free-agents; do
  [ -d "$base" ] || continue
  best=$(find "$base" -maxdepth 1 -mindepth 1 -type d | sort -V | tail -1)
  [ -n "$best" ] || continue
  installed=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("version",""))' \
    "$best/.claude-plugin/plugin.json" 2>/dev/null) || installed=""
  [ -n "$installed" ] && break
done
if [ "$installed" != "$V" ]; then
  say "✖ installed copy is at ${installed:-<none>}, not $V — the refresh did not land"
  say "  the published manifest is served by a CDN, so retry once after a short delay:"
  say "    get-haiggoh apply --only free-agents"
  exit 1
fi
say "✓ installed copy is $V"

# --- the Homebrew formula: a WARNING --------------------------------------------------------
# haiggoh/homebrew-tap's bump-formulas workflow points the formula at the asset just published.
# TRIGGER it now, then CHECK it capped, so a slow tap never holds anything hostage.
# Read through the API, never raw.githubusercontent.com: that CDN serves a cached copy for minutes
# and ignores a ?t= cache-buster, so the old check reported "not bumped" while the workflow had
# already pushed the new formula (2026-10-22: both 0.40.0 and 0.41.0 alarms).
triggered=0
if [ "${FA_SKIP_BREW:-0}" != 1 ]; then
  if gh workflow run bump-formulas.yml -R haiggoh/homebrew-tap -f formula=free-agents >/dev/null 2>&1; then
    triggered=1
    ok=0 waited=0 cap=${FA_BREW_WAIT:-120}
    while :; do
      if gh api repos/haiggoh/homebrew-tap/contents/Formula/free-agents.rb --jq .content 2>/dev/null \
           | base64 -d 2>/dev/null | grep -q "/download/v$V/free-agents-$V.tar.gz"; then ok=1; break; fi
      [ "$waited" -ge "$cap" ] && break
      sleep 10; waited=$((waited + 10))
    done
    if [ "$ok" = 1 ]; then say "brew: haiggoh/tap/free-agents -> $V"
    else say "⚠ tap formula not at $V after ${cap}s — it retries hourly; check: gh run list -R haiggoh/homebrew-tap --workflow bump-formulas.yml"
    fi
  else
    say "⚠ could not trigger bump-formulas.yml (it still runs hourly; FA_SKIP_BREW=1 to skip)"
  fi
else
  say "brew: skipped (FA_SKIP_BREW=1)"
fi
[ "$triggered" = 1 ] || true
exit 0
