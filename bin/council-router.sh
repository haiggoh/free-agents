#!/usr/bin/env bash
# council-router.sh — dynamic role router for free-agents
#
# Accepts --role <role_name> and selects the best available model from la_role bindings.
# Priority chain: api models first (if keys exist in $LA_API_KEYS_DIR, default ~/.api_keys/), falling
# back to local (MLX/CUDA)
# if API keys are missing or the API errors out.
#
# Usage: council-router.sh --role <role>
# Output: alias of the selected model (one line), or empty if none available

set -uo pipefail

# Resolve symlinks so invocation via a PATH symlink still finds this repo's config (portable).
_s="${BASH_SOURCE[0]}"; while [ -h "$_s" ]; do _d="$(cd -P "$(dirname "$_s")" && pwd)"; _s="$(readlink "$_s")"; case "$_s" in /*) ;; *) _s="$_d/$_s";; esac; done
BIN_DIR="$(cd -P "$(dirname "$_s")" && pwd)"
LA_ROOT="$(cd "$BIN_DIR/.." && pwd)"
# shellcheck source=/dev/null
. "$LA_ROOT/config/config-lib.sh"
la_load_config || exit 1

ROLE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    -h|--help)
      cat <<'HELP'
council-router.sh — dynamic role router for free-agents

Usage: council-router.sh --role <role>

Selects the best available model for a role from la_role bindings.
Priority: api models (if API keys exist) > local models (if weights on disk).

Output: the selected alias (one line), or empty if none available.

Environment:
  LA_API_KEYS_DIR   credential directory checked for <provider> key files (default ~/.api_keys).
                    Tests point this at a temp dir, so they never create or delete real keys.
HELP
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -n "$ROLE" ] || { echo "Usage: council-router.sh --role <role>" >&2; exit 2; }

# Get all bindings for this role (format: alias|effort|mode)
bindings=()
while IFS='|' read -r alias effort mode; do
  [ -n "$alias" ] || continue
  bindings+=("$alias|$effort|$mode")
done < <(la_role_bindings_for "$ROLE")

[ "${#bindings[@]}" -gt 0 ] || exit 0

# API key check cache
declare -A API_KEY_EXISTS

check_api_key() {
  local provider="$1"
  if [[ -v API_KEY_EXISTS[$provider] ]]; then
    return "${API_KEY_EXISTS[$provider]}"
  fi
  local key_file="${LA_API_KEYS_DIR:-$HOME/.api_keys}/$provider"
  if [ -f "$key_file" ] && [ -s "$key_file" ]; then
    API_KEY_EXISTS[$provider]=0
    return 0
  else
    API_KEY_EXISTS[$provider]=1
    return 1
  fi
}

# A binding's subdir names the model ("nvidia-nemotron-550b"); the key file is named after the
# PROVIDER ("nvidia"). Try the exact subdir first (a per-model key), then the provider prefix —
# the part before the first "-". Before 0.25.5 only the subdir was tried, so no API binding with
# a model-specific subdir could ever route.
check_api_key_for_subdir() {
  local sub="$1"
  check_api_key "$sub" && return 0
  local prov="${sub%%-*}"
  [ "$prov" != "$sub" ] && check_api_key "$prov"
}

# Phase 1: Try api backends with available keys (highest priority)
for binding in "${bindings[@]}"; do
  IFS='|' read -r alias effort mode <<<"$binding"
  serve="${LA_SERVE[$alias]:-}"
  if [ "$serve" = "api" ]; then
    provider="${LA_SUBDIR[$alias]:-}"
    if check_api_key_for_subdir "$provider"; then
      # Check if model is actually available (for api, la_on_disk just checks key)
      if la_on_disk "$alias"; then
        echo "$alias"
        exit 0
      fi
    fi
  fi
done

# Phase 2: Try litellm backends (if proxy is configured)
for binding in "${bindings[@]}"; do
  IFS='|' read -r alias effort mode <<<"$binding"
  serve="${LA_SERVE[$alias]:-}"
  if [ "$serve" = "litellm" ]; then
    # Check if litellm proxy is reachable
    if [ -n "${LA_LITELLM_CONFIG:-}" ] && [ -f "$LA_LITELLM_CONFIG" ]; then
      # Quick health check - try to reach the proxy
      if curl -s -f --max-time 2 "http://localhost:4141/health" >/dev/null 2>&1 || \
         curl -s -f --max-time 2 "http://localhost:4141/v1/models" >/dev/null 2>&1; then
        if la_on_disk "$alias"; then
          echo "$alias"
          exit 0
        fi
      fi
    fi
  fi
done

# Phase 3: Fall back to local backends (rapid, vllm, mlx_lm, llama_cpp)
for binding in "${bindings[@]}"; do
  IFS='|' read -r alias effort mode <<<"$binding"
  serve="${LA_SERVE[$alias]:-}"
  case "$serve" in
    rapid|vllm|mlx_lm|llama_cpp)
      if la_on_disk "$alias"; then
        echo "$alias"
        exit 0
      fi
      ;;
  esac
done

# No model available for this role
exit 0