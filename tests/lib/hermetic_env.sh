#!/bin/sh
# tests/lib/hermetic_env.sh — the ONE way a test spawns a clean-environment subprocess.
# 0.56.7 (L2): replaces the bare `env -i` call sites, which each hand-rolled their own
# passthrough list and often FORGOT the fence vars — so an `env -i` child re-resolved the
# REAL machine-wide limiter state (~/.claude/free-agents/.nvidia_throttle_state) instead of
# the per-session tmp file conftest forces. That is exactly the "gate tests off the real
# NVIDIA limiter" half of L2.
#
# Default passthrough: PATH, HOME, and every test-fence var conftest sets (throttle state,
# HF params cache, quota ledger, notify shim + sink). Extra KEY=VAL args before the command
# override or extend the environment. Empty fence vars are omitted, never passed empty
# (an empty LA_NVIDIA_THROTTLE_STATE would re-resolve the real default path).
#
# POSIX sh (no arrays): the KEY=VAL lists are word-split deliberately — values here never
# contain spaces. Usage: hermetic_env [KEY=VAL ...] [--] command [args...]
hermetic_env() {
  extra=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --) shift; break ;;
      *=*) extra="$extra $1"; shift ;;
      *) break ;;
    esac
  done
  if [ "$#" -eq 0 ]; then
    echo "hermetic_env: no command given" >&2
    return 2
  fi
  base="PATH=${PATH:-/usr/bin:/bin} HOME=${HOME:-/tmp}"
  for v in LA_NVIDIA_THROTTLE_STATE LA_HF_PARAMS_CACHE LA_QUOTA_LEDGER_DIR \
           FA_NOTIFY_SINK FA_NOTIFY_SHIM_DIR; do
    val=$(printenv "$v" 2>/dev/null || true)
    if [ -n "$val" ]; then
      base="$base $v=$val"
    fi
  done
  # shellcheck disable=SC2086  # base/extra are deliberately word-split KEY=VAL lists
  env -i $base $extra "$@"
}
