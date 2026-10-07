#!/usr/bin/env bash
# tests/lib/sandbox.sh — build a throwaway copy of the repo's RUNTIME for shell tests.
#
# Why: tests used to copy a hand-picked list of scripts into their sandbox. Every time a script
# gained a new `. "$DIR/../config/emoji.sh"` or `. "$DIR/la-hw-detect.sh"`, every fixture that
# copied that script broke — not because behaviour changed, but because the list went stale.
# This copies the WHOLE bin/ and the shared config libraries, so a new dependency is picked up
# automatically. The private overlay (config/*.local.*) is never copied: each test writes its
# own config.local.sh, so no test ever reads this machine's real model roster.
#
# Usage (source it, then call):
#   . "$REPO/tests/lib/sandbox.sh"
#   la_test_sandbox "$SB"                 # bin/ + config libraries
#   la_test_sandbox "$SB" remote-agents.sh local-capable-remote-models.psv   # + extra config files
#
# Environment:
#   LA_TEST_SANDBOX_REPO   repo to copy from (default: the checkout this file lives in)
#
# Run directly with --help for this text.

LA_TEST_SANDBOX_REPO="${LA_TEST_SANDBOX_REPO:-$(cd -P "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# Config files every launcher sources unconditionally. Data files (catalogues, rosters) are
# opt-in via extra arguments, because their presence changes what config-lib.sh derives.
LA_TEST_SANDBOX_CONFIG_LIBS=(config-lib.sh emoji.sh)

la_test_sandbox() {
  local dest="${1:?la_test_sandbox: destination directory required}"; shift
  mkdir -p "$dest/bin" "$dest/config" || return 1
  # Whole bin/, minus caches. cp -R keeps modes, so executables stay executable.
  (cd "$LA_TEST_SANDBOX_REPO/bin" && find . -path '*/__pycache__' -prune -o -path '*/.pytest_cache' -prune \
      -o -type f -print | while IFS= read -r f; do
        mkdir -p "$dest/bin/$(dirname "$f")" && cp -p "$f" "$dest/bin/$f"
      done) || return 1
  local f
  for f in "${LA_TEST_SANDBOX_CONFIG_LIBS[@]}" "$@"; do
    case "$f" in *.local.*) echo "la_test_sandbox: refusing private overlay $f" >&2; return 1 ;; esac
    cp -p "$LA_TEST_SANDBOX_REPO/config/$f" "$dest/config/$f" || return 1
  done
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    -h|--help) sed -n '2,/^$/{s/^# \{0,1\}//;p;}' "$0"; exit 0 ;;
    *) echo "tests/lib/sandbox.sh is a library: source it, or run with --help" >&2; exit 2 ;;
  esac
fi
