#!/usr/bin/env bash
# la-vram-preflight.sh — VRAM preflight check for CUDA inference (sibling to la-ram-preflight.sh).
#
# Uses nvidia-smi to check free VRAM. Fails gracefully if weights exceed capacity
# and UVM isn't configured. Does NOT launch any servers — only validates.
#
# Usage: la-vram-preflight.sh <alias> [effort-override]
#        la-vram-preflight.sh --profile <profile_id>
set -uo pipefail

# Resolve symlinks so invocation via a symlink still finds this repo's config (portable).
_s="${BASH_SOURCE[0]}"; while [ -h "$_s" ]; do _d="$(cd -P "$(dirname "$_s")" && pwd)"; _s="$(readlink "$_s")"; case "$_s" in /*) ;; *) _s="$_d/$_s";; esac; done
PREFLIGHT_DIR="$(cd -P "$(dirname "$_s")" && pwd)"
# shellcheck source=/dev/null
. "$PREFLIGHT_DIR/../config/config-lib.sh"
# shellcheck source=/dev/null
. "$PREFLIGHT_DIR/la-hw-detect.sh"

la_load_config || exit 1

# --- Argument parsing ---------------------------------------------------------
USE_PROFILE=false
PROFILE_ID=""
MODEL_NAME=""
EFFORT_OVERRIDE=""

while (( $# > 0 )); do
    case "$1" in
        --profile)
            USE_PROFILE=true
            PROFILE_ID="${2:-}"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        *)
            if [ -z "$MODEL_NAME" ]; then
                MODEL_NAME="$1"
            elif [ -z "$EFFORT_OVERRIDE" ]; then
                EFFORT_OVERRIDE="$1"
            fi
            shift
            ;;
    esac
done

if [ "$USE_PROFILE" = "true" ]; then
    if [ -z "$PROFILE_ID" ]; then
        echo "❌ --profile requires a PROFILE_ID" >&2
        exit 1
    fi
    # Resolve profile through canonical resolver
    RESOLVED_JSON=$(python3 "$PREFLIGHT_DIR/la-model-profile.py" resolve "$PROFILE_ID" --json 2>/dev/null) || {
        echo "❌ Failed to resolve profile '$PROFILE_ID'" >&2
        exit 1
    }

    ARTIFACT_ID=$(printf '%s' "$RESOLVED_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("artifact_id",""))')
    BACKEND=$(printf '%s' "$RESOLVED_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("backend",""))')
    ENV_PROFILE=$(printf '%s' "$RESOLVED_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("environment_profile",""))')
    RES_PROFILE=$(printf '%s' "$RESOLVED_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("resource_profile",""))')
    THINK=$(printf '%s' "$RESOLVED_JSON" | python3 -c 'import sys,json; print(str(json.load(sys.stdin).get("thinking",False)).lower())')
    TOOLP=$(printf '%s' "$RESOLVED_JSON" | python3 -c 'import sys,json; p=json.load(sys.stdin).get("tool_parser"); print(p if p else "")')
    REASONP=$(printf '%s' "$RESOLVED_JSON" | python3 -c 'import sys,json; p=json.load(sys.stdin).get("reasoning_parser"); print(p if p else "")')
    FALLBACK=$(printf '%s' "$RESOLVED_JSON" | python3 -c 'import sys,json; p=json.load(sys.stdin).get("fallback"); print(p if p else "")')

    # Find the registry alias that maps to this artifact_id
    REGISTRY_ALIAS=""
    for alias in "${!LA_SUBDIR[@]}"; do
        if [ "${LA_SUBDIR[$alias]}" = "$ARTIFACT_ID" ]; then
            REGISTRY_ALIAS="$alias"
            break
        fi
    done

    if [ -z "$REGISTRY_ALIAS" ]; then
        echo "❌ No registry alias found for artifact '$ARTIFACT_ID' (profile: $PROFILE_ID)" >&2
        exit 1
    fi

    MODEL_NAME="$REGISTRY_ALIAS"
else
    if [ -z "$MODEL_NAME" ] || ! la_lookup "$MODEL_NAME"; then
        la_retired_hint "$MODEL_NAME" || true
        echo "Usage: $0 [--profile PROFILE_ID] <alias> [effort]"; echo "Registered aliases:"; la_aliases_help; exit 1
    fi
fi

MODEL_DIR="$LA_CUR_DIR"; SPOOF_NAME="$LA_CUR_SPOOF"; SERVE="$LA_CUR_SERVE"
TOOLP="$LA_CUR_TOOLP"; REASONP="$LA_CUR_REASONP"; THINK="$LA_CUR_THINK"
RAPID_SPEC_CONFIG="$LA_CUR_RAPID_SPEC_CONFIG"
SPOOF_PRIMARY="${SPOOF_NAME%%,*}"

# Override with profile-resolved values if in profile mode
if [ "$USE_PROFILE" = "true" ]; then
    SERVE="$BACKEND"
    TOOLP="${TOOLP:-$LA_CUR_TOOLP}"
    REASONP="${REASONP:-$LA_CUR_REASONP}"
    THINK="${THINK:-$LA_CUR_THINK}"
fi

if [ ! -d "$MODEL_DIR" ]; then echo "❌ model dir not found: $MODEL_DIR (check LA_MODELS_DIR / subdir in config)"; exit 1; fi
if [ -z "$(find -L "$MODEL_DIR" -type f -size +1024k -print -quit 2>/dev/null)" ]; then
    echo "❌ no weight files in $MODEL_DIR — the directory exists but holds no model (metadata-only shell)."
    echo "   This is NOT the same as 'not downloaded': something is there, so a re-download may skip it."
    echo "   Inspect with bin/la-disk-inventory.sh --empty, then re-fetch or repoint the alias."
    exit 1
fi

# --- Hardware detection -------------------------------------------------------
# Source la-hw-detect.sh
# shellcheck source=/dev/null
. "$PREFLIGHT_DIR/la-hw-detect.sh"

echo "🔍 VRAM Preflight — $MODEL_NAME"
echo "   Hardware: $LA_HARDWARE"
[ -n "${LA_VRAM_GB:-}" ] && echo "   VRAM: ${LA_VRAM_GB} GB" || echo "   VRAM: unknown"
[ "${LA_CUDA_WARNING:-0}" = "1" ] && echo "   ⚠️  CUDA detection warning (nvidia-smi failed on Linux/WSL)"

# --- VRAM Preflight -----------------------------------------------------------
# Only meaningful on CUDA hardware
if [ "$LA_HARDWARE" != "cuda" ]; then
    echo "ℹ️  Non-CUDA hardware ($LA_HARDWARE) — VRAM preflight skipped."
    exit 0
fi

if [ -z "${LA_VRAM_GB:-}" ] || [ "$LA_VRAM_GB" -eq 0 ]; then
    echo "❌ VRAM size unknown (LA_VRAM_GB not set). Cannot perform preflight." >&2
    exit 1
fi

VRAM_GB="$LA_VRAM_GB"
echo "🔍 Checking VRAM capacity: ${VRAM_GB} GB"

# Get free VRAM from nvidia-smi
FREE_VRAM_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1 | awk '{print $1}')
if [ -z "$FREE_VRAM_MB" ] || ! [[ "$FREE_VRAM_MB" =~ ^[0-9]+$ ]]; then
    echo "❌ Failed to query free VRAM via nvidia-smi." >&2
    exit 1
fi
FREE_VRAM_GB=$(( FREE_VRAM_MB / 1024 ))
TOTAL_VRAM_GB="$LA_VRAM_GB"
USED_VRAM_GB=$(( TOTAL_VRAM_GB - FREE_VRAM_GB ))

echo "   Total VRAM: ${TOTAL_VRAM_GB} GB"
echo "   Free VRAM:  ${FREE_VRAM_GB} GB (${FREE_VRAM_MB} MB)"
echo "   Used VRAM:  ${USED_VRAM_GB} GB"

# --- Estimate model VRAM requirement ------------------------------------------
# Rough estimates based on model size and quantization
# This is a rough guard rail; actual usage depends on context length, batch size, etc.
MODEL_SIZE_GB="${LA_SIZE[$MODEL_NAME]:-?}"
if [[ "$MODEL_SIZE_GB" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    SIZE_NUM=$(LC_ALL=C awk -v s="$MODEL_SIZE_GB" 'BEGIN{print s+0}')
    # Rough VRAM estimate: model weights + KV cache + overhead
    # For 4-bit: ~1.5x model size (weights + KV cache)
    # For 8-bit: ~2x model size
    # Add 20% overhead for CUDA context, fragmentation, etc.
    EST_VRAM_GB=$(LC_ALL=C awk -v s="$SIZE_NUM" 'BEGIN{printf "%.1f", s * 1.6}')
    echo "   Estimated VRAM needed (model + KV + overhead): ~${EST_VRAM_GB} GB"

    # Check if free VRAM is sufficient
    if [ "$FREE_VRAM_GB" -lt "${EST_VRAM_GB%.*}" ]; then
        SHORTFALL=$(( ${EST_VRAM_GB%.*} - FREE_VRAM_GB ))
        echo "⚠️  VRAM may be insufficient: need ~${EST_VRAM_GB} GB, have ${FREE_VRAM_GB} GB free (shortfall ~${SHORTFALL} GB)"
        echo "   Consider: closing other GPU apps, reducing context length, or using UVM offload (Tier B)."
        # Don't exit - just warn. The runtime will try UVM offload for Tier B.
    else
        echo "✅ Sufficient free VRAM (${FREE_VRAM_GB} GB >= ~${EST_VRAM_GB} GB needed)"
    fi
else
    echo "   Model size unknown, skipping VRAM estimate."
fi

# --- CUDA Tier check ----------------------------------------------------------
# Source la-hw-detect.sh logic inline (already sourced)
if [ "$LA_HARDWARE" = "cuda" ] && [ -n "${LA_VRAM_GB:-}" ] && [ "$LA_VRAM_GB" -gt 0 ]; then
    if [ "$LA_VRAM_GB" -ge 16 ]; then
        echo "🎮 CUDA Tier A (VRAM >= 16GB) — vLLM fully VRAM-resident recommended"
    else
        echo "🎮 CUDA Tier B (VRAM <= 8GB) — UVM offload (vLLM --swap-space) or llama-server -ngl recommended"
        if [ "$FREE_VRAM_GB" -lt 4 ]; then
            echo "⚠️  Very low free VRAM (${FREE_VRAM_GB} GB) — Tier B offload may struggle."
        fi
    fi
fi

# --- Check for UVM offload capability -----------------------------------------
# Check if vLLM supports --swap-space (UVM offload)
VLLM_BIN="${LA_CUDA_BIN:-$(command -v vllm 2>/dev/null || echo "")}"
if [ -n "$VLLM_BIN" ] && [ -x "$VLLM_BIN" ]; then
    if "$VLLM_BIN" serve --help 2>&1 | grep -q -- '--swap-space'; then
        echo "✅ vLLM supports --swap-space (UVM offload available for Tier B)"
    else
        echo "⚠️  vLLM does not support --swap-space (UVM offload not available in this build)"
    fi
fi

# --- Check llama-server for -ngl support --------------------------------------
LLAMA_SERVER="${LA_LLAMA_SERVER:-$(command -v llama-server 2>/dev/null || echo "")}"
if [ -n "$LLAMA_SERVER" ] && [ -x "$LLAMA_SERVER" ]; then
    if "$LLAMA_SERVER" -h 2>&1 | grep -q -- '-ngl'; then
        echo "✅ llama-server supports -ngl (GPU layer offload for Tier B GGUF)"
    else
        echo "⚠️  llama-server does not support -ngl (no GPU layer offload)"
    fi
fi

echo ""
echo "✅ VRAM preflight complete for $MODEL_NAME"
exit 0