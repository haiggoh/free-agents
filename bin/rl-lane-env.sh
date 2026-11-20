#!/usr/bin/env bash
# rl-lane-env.sh — print the export lines for the NVIDIA rate-limiter lane flags, so a
# launcher can eval them into the proxy's environment (and a user can mark a manually
# started session):
#   rl-lane-env.sh --priority        -> export LA_LIMITER_PRIORITY=1   (priority lane)
#   rl-lane-env.sh --kind dispatch   -> export LA_LIMITER_KIND=dispatch (ladder kind)
# Usage: eval "$(bin/rl-lane-env.sh --priority)"      --help prints this text.
set -u
if [ "${1:-}" = "--help" ]; then
  sed -n '2,7p' "$0"
  exit 0
fi
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --priority)
      out="${out}export LA_LIMITER_PRIORITY=1\\n" ;;
    --kind)
      shift
      k="${1:-}"
      case "$k" in
        interactive|dispatch|background) out="${out}export LA_LIMITER_KIND=$k\\n" ;;
        *) echo "rl-lane-env.sh: --kind needs interactive|dispatch|background, got '$k'" >&2; exit 2 ;;
      esac ;;
    *) echo "rl-lane-env.sh: unknown flag '$1' (try --help)" >&2; exit 2 ;;
  esac
  shift
done
printf '%b' "$out"
