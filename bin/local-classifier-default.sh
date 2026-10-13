#!/usr/bin/env bash
# local-classifier-default.sh — resolve the default LOCAL Auto Mode classifier model.
#
# Qualification order from the 2026-10-13 campaign
# (docs/classifier-campaign-2026-10-13.md): granite-4.2-30b is the only fully qualified
# classifier on the pinned rapid-mlx (DIRECT_PASS, 99.065% changed-prefix reuse). The
# fallbacks are the campaign's latency tier: zero cache reuse, but a full ~38k-token
# re-prefill still fits Claude Code's 45s classifier deadline. Prints the first
# candidate whose weights are installed; warns when nothing classifier-capable exists.
#
# Usage:
#   local-classifier-default.sh                print the selected model DIR (exit 0)
#   local-classifier-default.sh --alias        print the selected ALIAS instead
#   local-classifier-default.sh --dir-only     on failure print nothing, no warning (exit 2)
#   local-classifier-default.sh --help
#
# Environment:
#   LA_MODELS_DIR                  models dir (default ~/.models)
#   LA_LOCAL_CLASSIFIER_ALIAS      primary candidate alias (default granite-4.2-30b)
#   LA_LOCAL_CLASSIFIER_FALLBACKS  comma-separated fallback aliases (default:
#                                  kat-coder-optiq,qwen-3.6-35b-a3b,ornith-1.5-35b,
#                                  nemotron35-lightning,agentworld-35b)
#
# Exit codes: 0 = selected; 2 = none installed (stderr carries the install hint).
# Keep the alias->subdir map in sync with the config registrations, and extend BOTH
# lists only with models that passed the qualification campaign.
set -uo pipefail

MODELS_DIR="${LA_MODELS_DIR:-$HOME/.models}"
PRIMARY="${LA_LOCAL_CLASSIFIER_ALIAS:-granite-4.2-30b}"
FALLBACKS="${LA_LOCAL_CLASSIFIER_FALLBACKS:-kat-coder-optiq,qwen-3.6-35b-a3b,ornith-1.5-35b,nemotron35-lightning,agentworld-35b}"

case "${1:-}" in
  -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
esac

subdir_for() {
  case "$1" in
    granite-4.2-30b)        echo granite-4.2-30b-q4-mlx ;;
    kat-coder-optiq)        echo KAT-Coder-V2.5-Dev-OptiQ-4bit ;;
    qwen-3.6-35b-a3b)       echo Qwen3.6-35B-A3B-4bit ;;
    ornith-1.5-35b)         echo Ornith-1.5-35B-A3B-MLX-4bit ;;
    nemotron35-lightning)   echo nemotron35-lightning-mlx4 ;;
    agentworld-35b)         echo qwen-agentworld-4bit ;;
    *)                      return 1 ;;
  esac
}

DIR_ONLY=0
ALIAS_OUT=0
for arg in "$@"; do
  case "$arg" in
    --dir-only) DIR_ONLY=1 ;;
    --alias)    ALIAS_OUT=1 ;;
    *)          echo "local-classifier-default: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

IFS=',' read -r -a candidates <<< "$PRIMARY,$FALLBACKS"
for alias in "${candidates[@]}"; do
  [ -n "$alias" ] || continue
  subdir="$(subdir_for "$alias")" || {
    echo "local-classifier-default: no subdir mapping for alias '$alias' — extend subdir_for()" >&2
    continue
  }
  dir="$MODELS_DIR/$subdir"
  if [ -f "$dir/config.json" ]; then
    if [ "$ALIAS_OUT" = 1 ]; then
      echo "$alias"
    else
      echo "$dir"
    fi
    exit 0
  fi
done

if [ "$DIR_ONLY" = 0 ]; then
  echo "WARNING: no classifier-capable local model is installed — Auto Mode needs one." >&2
  echo "  Install the recommended model:  install/download-models.sh --select granite-4.2-30b" >&2
  echo "  Or run blind-trust mode (auto mode off) until one is installed." >&2
fi
exit 2
