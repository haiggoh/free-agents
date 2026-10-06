#!/usr/bin/env bash
# local-llm-hotswap.sh — land a registered model on the first FREE port and print SUCCESS_PORT.
#
# Config-driven: the model registry + machine settings come from config/config.local.sh
# (your private overlay) or config/config.example.sh (shipped defaults). SAFE by design — it
# scans ports, reuses a healthy matching server, reclaims only frozen zombies, and NEVER kills a
# healthy model already running on another port (so it can't shut down a live local session).
set -uo pipefail

# Resolve symlinks so invocation via a symlink still finds this repo's config (portable).
_s="${BASH_SOURCE[0]}"; while [ -h "$_s" ]; do _d="$(cd -P "$(dirname "$_s")" && pwd)"; _s="$(readlink "$_s")"; case "$_s" in /*) ;; *) _s="$_d/$_s";; esac; done
HOTSWAP_DIR="$(cd -P "$(dirname "$_s")" && pwd)"
# shellcheck source=/dev/null
. "$HOTSWAP_DIR/../config/config-lib.sh"
la_load_config || exit 1

# --- Profile-aware mode ---------------------------------------------------------
# If --profile is given, resolve through the canonical resolver and extract
# the model alias, backend, environment, resource profile, artifact path, etc.
# This replaces the direct registry lookup with a profile-aware resolution.
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

# If using profile mode, resolve the profile and find the corresponding registry alias
if [ "$USE_PROFILE" = "true" ]; then
    if [ -z "$PROFILE_ID" ]; then
        echo "❌ --profile requires a PROFILE_ID" >&2
        exit 1
    fi
    # Resolve profile through canonical resolver
    RESOLVED_JSON=$(python3 "$HOTSWAP_DIR/la-model-profile.py" resolve "$PROFILE_ID" --json 2>/dev/null) || {
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
    # Search LA_SUBDIR for the matching artifact
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
    export LA_PROFILE_ID="$PROFILE_ID"
    export LA_PROFILE_ARTIFACT_ID="$ARTIFACT_ID"
    export LA_PROFILE_BACKEND="$BACKEND"
    export LA_PROFILE_ENV="$ENV_PROFILE"
    export LA_PROFILE_RESOURCE="$RES_PROFILE"
    export LA_PROFILE_THINK="$THINK"
    export LA_PROFILE_TOOLP="$TOOLP"
    export LA_PROFILE_REASONP="$REASONP"
    export LA_PROFILE_FALLBACK="$FALLBACK"
fi

LOG_FILE_BASE="$HOME/.claude/logs/vllm"
CONFIG_DIR="$HOME/.claude/logs/local-agents-configs"
mkdir -p "$CONFIG_DIR" "$(dirname "$LOG_FILE_BASE")"

# Base flag for EVERY vllm tier. --enable-auto-tool-choice is what surfaces structured tool_calls;
# without it the model's commands leak into chat as markdown. Per-model --tool-call-parser is added
# from the registry. Concurrent Claude Code requests (main turn + background calls) hit the
# single-slot SimpleEngine; LA_ADMISSION=wait makes overflow QUEUE instead of erroring (EngineBusy).
export VLLM_MLX_SIMPLE_ENGINE_LOCK_ADMISSION="$LA_ADMISSION"

# Accept a ROLE NAME as well as an alias (see la_resolve_target); an alias always wins.
if [ -n "$MODEL_NAME" ] && [ "$USE_PROFILE" = "false" ]; then
    _resolved="$(la_resolve_target "$MODEL_NAME" 2>/dev/null || true)"
    if [ -n "$_resolved" ] && [ "$_resolved" != "$MODEL_NAME" ]; then
        echo "🎯 role '$MODEL_NAME' -> $_resolved" >&2
        MODEL_NAME="$_resolved"
    fi
fi

if [ "$USE_PROFILE" = "true" ]; then
    # Use the registry alias we found for the profile's artifact
    if [ -z "$MODEL_NAME" ] || ! la_lookup "$MODEL_NAME"; then
        echo "❌ Could not find registry alias for artifact '$ARTIFACT_ID' (profile: $PROFILE_ID)" >&2
        exit 1
    fi
else
    if [ -z "$MODEL_NAME" ] || ! la_lookup "$MODEL_NAME"; then
        la_retired_hint "$MODEL_NAME" || true
        echo "Usage: $0 [--profile PROFILE_ID] <alias> [effort]"; echo "Registered aliases:"; la_aliases_help; exit 1
    fi
fi

MODEL_DIR="$LA_CUR_DIR"; SPOOF_NAME="$LA_CUR_SPOOF"; SERVE="$LA_CUR_SERVE"
TOOLP="$LA_CUR_TOOLP"; REASONP="$LA_CUR_REASONP"; THINK="$LA_CUR_THINK"
RAPID_SPEC_CONFIG="$LA_CUR_RAPID_SPEC_CONFIG"
RAPID_SPEC_CONFIG_SHA256="$(
    printf '%s' "$RAPID_SPEC_CONFIG" |
        /usr/bin/shasum -a 256 |
        awk '{print $1}'
)"
SPOOF_PRIMARY="${SPOOF_NAME%%,*}"

# Override with profile-resolved values if in profile mode
if [ "$USE_PROFILE" = "true" ]; then
    SERVE="$BACKEND"
    TOOLP="${TOOLP:-$LA_CUR_TOOLP}"
    REASONP="${REASONP:-$LA_CUR_REASONP}"
    THINK="${THINK:-$LA_CUR_THINK}"
fi

# State the backend BEFORE anything loads, and say where it came from.
echo "🔧 backend: $(la_serve_display "$MODEL_NAME")   (machine default: $LA_DEFAULT_MLX_BACKEND)"
if [ "$SERVE" = "vllm" ]; then
    echo "   ℹ️  vllm-mlx is the LEGACY lane — kept for A/B against recorded evidence. The default is rapid."
fi
if [ ! -d "$MODEL_DIR" ]; then echo "❌ model dir not found: $MODEL_DIR (check LA_MODELS_DIR / subdir in config)"; exit 1; fi
if [ -z "$(find -L "$MODEL_DIR" -type f -size +1024k -print -quit 2>/dev/null)" ]; then
    echo "❌ no weight files in $MODEL_DIR — the directory exists but holds no model (metadata-only shell)."
    echo "   This is NOT the same as 'not downloaded': something is there, so a re-download may skip it."
    echo "   Inspect with bin/la-disk-inventory.sh --empty, then re-fetch or repoint the alias."
    exit 1
fi

# --- bounded readiness wait (dumps log tail + PID liveness on timeout; validates identity) ----
wait_ready() {
    local port="$1" logf="$2" label="${3:-server}" pid="${4:-}" expected_id="${5:-}"
    local timeout="${HOTSWAP_READY_TIMEOUT:-480}"
    local deadline=$(( SECONDS + timeout ))
    until curl -s --max-time 2 "http://localhost:$port/v1/models" > /dev/null 2>&1; do
        if (( SECONDS >= deadline )); then
            { echo "❌ $label startup FAILED — /v1/models silent after ${timeout}s (port $port, log $logf)"
              if [ -n "$pid" ]; then kill -0 "$pid" 2>/dev/null \
                 && echo "   pid $pid ALIVE (loading/wedged)" || echo "   pid $pid DEAD (crashed — see log)"; fi
              echo "   --- last 80 log lines ---"; tail -n 80 "$logf" 2>/dev/null; } >&2
            exit 1
        fi
        sleep 2
    done
    if [ -n "$expected_id" ]; then
        local observed
        observed=$(curl -s --max-time 4 "http://localhost:$port/v1/models" 2>/dev/null | grep -oE '"id"[[:space:]]*:[[:space:]]*"[^"]*"' | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/' | tr '\n' ' ')
        case " $observed " in *" $expected_id "*) : ;; *) echo "⚠️  $label: expected id '$expected_id' not in /v1/models (observed: ${observed:-none})." >&2 ;; esac
    fi
}

_preflight_warmup() {
    local port="$1" spoof="$2" timeout="${3:-$(( ${LA_SERVER_TIMEOUT_S:-300} / 4 ))}"
    [ "${LA_HOTSWAP_PREFLIGHT:-1}" = "0" ] && return 0
    local start=$SECONDS resp code ms
    resp=$(curl -s --max-time "$timeout" -X POST "http://localhost:$port/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$spoof\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":3}") || true
    ms=$(( (SECONDS - start) * 1000 ))
    code=$(printf '%s' "$resp" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("choices",[{}])[0].get("finish_reason","?"))' 2>/dev/null || echo "?")
    if [ "$code" = "?" ] || [ -z "$code" ]; then
        echo "⚠️  Preflight warmup FAILED (finish_reason=$code, ${ms}ms) — server may still be initializing."
        echo "   First caller turn may pay the full cold-prefill penalty."
        return 1
    fi
    echo "✅ Preflight warmup OK (finish_reason=$code, ${ms}ms)"
    return 0
}

echo "Scanning ports $LA_PORT_START-$LA_PORT_MAX for a free slot (config: ${LA_CONFIG_SOURCE})..."
TARGET_PORT=""
for ((port=LA_PORT_START; port<=LA_PORT_MAX; port++)); do
    if lsof -i :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        CURRENT_IDS=$(curl -s --max-time 4 "http://localhost:$port/v1/models" | grep -oE '"id"[[:space:]]*:[[:space:]]*"[^"]*"' | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
        CURRENT_MODEL=$(printf '%s\n' "$CURRENT_IDS" | head -n 1)

        if [ "$SERVE" = "rapid" ]; then
            _meta="$CONFIG_DIR/server_${port}.meta"
            _listener_pid=$(lsof -t -i ":$port" -sTCP:LISTEN 2>/dev/null | head -1)
            _meta_backend=$(awk -F= '$1=="backend"{print substr($0,index($0,"=")+1)}' "$_meta" 2>/dev/null)
            _meta_alias=$(awk -F= '$1=="alias"{print substr($0,index($0,"=")+1)}' "$_meta" 2>/dev/null)
            _meta_model=$(awk -F= '$1=="model_dir"{print substr($0,index($0,"=")+1)}' "$_meta" 2>/dev/null)
            _meta_spec=$(awk -F= '$1=="spec_config_sha256"{print substr($0,index($0,"=")+1)}' "$_meta" 2>/dev/null)
            _meta_pid=$(awk -F= '$1=="pid"{print $2}' "$_meta" 2>/dev/null)
            if [ "${LA_HOTSWAP_FORCE_FRESH:-0}" != "1" ] &&
               [ "$_meta_backend" = "rapid" ] &&
               [ "$_meta_alias" = "$MODEL_NAME" ] &&
               [ "$_meta_model" = "$MODEL_DIR" ] &&
               [ "$_meta_spec" = "$RAPID_SPEC_CONFIG_SHA256" ] &&
               [ -n "$_listener_pid" ] &&
               [ "$_listener_pid" = "$_meta_pid" ] &&
               printf '%s\n' "$CURRENT_IDS" | grep -qxF "$SPOOF_PRIMARY"; then
                echo "✅ $MODEL_NAME already healthy via Rapid-MLX on port $port."
                echo "SUCCESS_PORT=$port"; exit 0
            fi
        fi

        if [ -n "$CURRENT_MODEL" ]; then HEALTH="alive:$CURRENT_MODEL"; else
            HEALTH=$(curl -s --max-time 25 -X POST "http://localhost:$port/v1/chat/completions" -H "Content-Type: application/json" \
                     -d '{"model":"probe","messages":[{"role":"user","content":"p"}],"max_tokens":1}'); fi
        if [ -z "$HEALTH" ]; then
            echo "⚠️ Port $port frozen (zombie). Reclaiming..."; TARGET_PID=$(lsof -t -i :$port -sTCP:LISTEN)
            [ -n "$TARGET_PID" ] && { kill -9 "$TARGET_PID"; sleep 1; }; TARGET_PORT=$port; break
        elif printf '%s\n' "$CURRENT_IDS" | grep -qxF "$MODEL_NAME" || printf '%s\n' "$CURRENT_IDS" | grep -qxF "$MODEL_DIR"; then
            echo "✅ $MODEL_NAME already healthy on port $port."
            if [ "${LA_HOTSWAP_FORCE_FRESH:-0}" = "1" ]; then
                echo "   (LA_HOTSWAP_FORCE_FRESH=1 — restarting to pick up changed config)"
                _pid=$(lsof -t -i ":$port" -sTCP:LISTEN 2>/dev/null | head -1)
                [ -n "$_pid" ] && { kill -9 "$_pid"; sleep 1; }
                TARGET_PORT=$port; break
            fi
            _pid=$(lsof -t -i ":$port" -sTCP:LISTEN 2>/dev/null | head -1)
            _rt=$(ps -o command= -p "${_pid:-0}" 2>/dev/null | grep -o -- "--timeout [0-9.]*" | awk '{print $2}')
            if [ -n "$_rt" ] && [ "${_rt%%.*}" -lt "$LA_SERVER_TIMEOUT_S" ] 2>/dev/null; then
                echo "⚠️  That server runs --timeout ${_rt}s, below this config's ${LA_SERVER_TIMEOUT_S}s."
                echo "    Streaming turns longer than ${_rt}s get killed server-side and retried."
                echo "    Restart it to pick up the current setting:  kill $_pid  (then relaunch)"
            elif [ -z "$_rt" ]; then
                echo "⚠️  That server was started without --timeout, so it uses vllm-mlx's 300s default:"
                echo "    any streaming turn over 5 min is killed server-side, then retried from scratch."
                echo "    Restart it to pick up ${LA_SERVER_TIMEOUT_S}s:  kill ${_pid:-<pid on port $port>}  (then relaunch)"
            fi
            echo "SUCCESS_PORT=$port"; exit 0
        fi
        echo "ℹ️ Port $port busy serving '$CURRENT_MODEL'. Skipping..."
    else TARGET_PORT=$port; break; fi
done
[ -z "$TARGET_PORT" ] && { echo "❌ All ports $LA_PORT_START-$LA_PORT_MAX saturated."; exit 1; }
LOG_FILE="${LOG_FILE_BASE}_${TARGET_PORT}.log"

# --- RAM PREFLIGHT: only reached when a NEW server is about to load weights --------------------
if [ -x "$HOTSWAP_DIR/la-ram-preflight.sh" ]; then
    if ! "$HOTSWAP_DIR/la-ram-preflight.sh" "$MODEL_NAME"; then
        echo
        echo "🛑 Not launching $MODEL_NAME on port $TARGET_PORT — see the RAM preflight above."
        echo "   Nothing was killed. Free memory yourself, or retry once a server exits."
        echo "   Override with LA_SKIP_RAM_PREFLIGHT=1 if you are certain the numbers are wrong."
        [ "${LA_SKIP_RAM_PREFLIGHT:-0}" = "1" ] || exit 1
        echo "   LA_SKIP_RAM_PREFLIGHT=1 set — continuing at your own risk."
    fi
fi

# --- Rapid-MLX branch ---------------------------------------------------------
if [ "$SERVE" = "rapid" ]; then
    if [ ! -x "$LA_RAPID_BIN" ]; then
        echo "❌ Rapid-MLX executable not found or not executable: ${LA_RAPID_BIN:-<none discovered>}" >&2
        echo "   Rapid is the DEFAULT backend, so this stops every generic registration. Fix by either:" >&2
        echo "     brew install rapid-mlx                    (maintained install, discovered automatically)" >&2
        echo "     LA_RAPID_BIN=<path> in config.local.sh     (pin an exact version, e.g. an isolated venv)" >&2
        echo "   Or set LA_DEFAULT_MLX_BACKEND=vllm in config.local.sh to fall back to the legacy lane." >&2
        exit 1
    fi

    RAPID_META="$CONFIG_DIR/server_${TARGET_PORT}.meta"
    RAPID_META_TMP="${RAPID_META}.tmp.$$"

    # Override Rapid config from profile if available
    if [ "$USE_PROFILE" = "true" ] && [ -n "$RES_PROFILE" ]; then
        # Read resource profile for Rapid config
        RES_JSON=$(python3 "$HOTSWAP_DIR/la-model-profile.py" resolve "$RES_PROFILE" --json 2>/dev/null || echo '{}')
        CACHE_MB=$(printf '%s' "$RES_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("cache_memory_mb", ""))')
        HYBRID=$(printf '%s' "$RES_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("hybrid_cache_entries", ""))')
        PIN_SYS=$(printf '%s' "$RES_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("pin_system_prompt", ""))')
        RELOCATE=$(printf '%s' "$RES_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("relocate_mid_system", ""))')
        PFLASH=$(printf '%s' "$RES_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("pflash", ""))')
        MAX_SEQS=$(printf '%s' "$RES_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("max_num_seqs", ""))')
        [ -n "$CACHE_MB" ] && LA_RAPID_CACHE_MEMORY_MB="$CACHE_MB"
        [ -n "$HYBRID" ] && LA_RAPID_HYBRID_CACHE_ENTRIES="$HYBRID"
        [ -n "$PIN_SYS" ] && LA_RAPID_PIN_SYSTEM_PROMPT="$PIN_SYS"
        [ -n "$RELOCATE" ] && LA_RAPID_RELOCATE_MID_SYSTEM="$RELOCATE"
        [ -n "$PFLASH" ] && LA_RAPID_PFLASH="$PFLASH"
        [ -n "$MAX_SEQS" ] && LA_RAPID_MAX_NUM_SEQS="$MAX_SEQS"
    fi

    RAPID_CMD=(
        "$LA_RAPID_BIN" --no-telemetry
        serve "$MODEL_DIR"
        --served-model-name "$SPOOF_PRIMARY"
        --host 127.0.0.1
        --port "$TARGET_PORT"
        --max-num-seqs "$LA_RAPID_MAX_NUM_SEQS"
        --max-concurrent-requests "$LA_RAPID_MAX_CONCURRENT_REQUESTS"
        --cache-memory-mb "$LA_RAPID_CACHE_MEMORY_MB"
        --hybrid-cache-entries "$LA_RAPID_HYBRID_CACHE_ENTRIES"
        --timeout "$LA_SERVER_TIMEOUT_S"
        --no-mllm
        --pflash "$LA_RAPID_PFLASH"
    )

    if [ -n "$RAPID_SPEC_CONFIG" ]; then
        RAPID_CMD+=(--speculative-config "$RAPID_SPEC_CONFIG")
    else
        RAPID_CMD+=(--no-spec-decode)
    fi

    case "$LA_RAPID_PIN_SYSTEM_PROMPT" in
        true|1|yes) RAPID_CMD+=(--pin-system-prompt) ;;
    esac
    case "$LA_RAPID_RELOCATE_MID_SYSTEM" in
        true|1|yes) RAPID_CMD+=(--relocate-mid-conversation-system) ;;
    esac

    RAPID_TOOLP="$TOOLP"
    case "$RAPID_TOOLP" in
        qwen|qwen3_coder) RAPID_TOOLP="qwen3_coder_xml" ;;
    esac
    if [ -n "$RAPID_TOOLP" ]; then
        RAPID_CMD+=(--enable-auto-tool-choice --tool-call-parser "$RAPID_TOOLP")
    fi

    if [ "$THINK" = "true" ]; then
        RAPID_CMD+=(--reasoning-parser "${REASONP:-qwen3}")
        RAPID_CMD+=(--default-temperature 0.6 --default-top-p 0.95)
    else
        RAPID_CMD+=(--no-thinking --no-reasoning-parser)
    fi

    echo "🚀 Launching $MODEL_NAME via Rapid-MLX on free port $TARGET_PORT  (🧠 thinking: $THINK)..."
    RAPID_MLX_TELEMETRY=0 nohup "${RAPID_CMD[@]}" > "$LOG_FILE" 2>&1 &
    RAPID_PID=$!

    {
        echo "backend=rapid"
        echo "alias=$MODEL_NAME"
        echo "model_dir=$MODEL_DIR"
        echo "served_id=$SPOOF_PRIMARY"
        echo "spec_config_sha256=$RAPID_SPEC_CONFIG_SHA256"
        echo "pid=$RAPID_PID"
        [ "$USE_PROFILE" = "true" ] && echo "profile_id=$PROFILE_ID"
    } > "$RAPID_META_TMP"
    mv -f "$RAPID_META_TMP" "$RAPID_META"

    wait_ready "$TARGET_PORT" "$LOG_FILE" "$MODEL_NAME" "$RAPID_PID" "$SPOOF_PRIMARY"
    tail -n 12 "$LOG_FILE"
    _preflight_warmup "$TARGET_PORT" "$SPOOF_PRIMARY"
    echo "DISPATCH_MODEL=$SPOOF_PRIMARY"
    echo "SUCCESS_PORT=$TARGET_PORT"
    [ "$USE_PROFILE" = "true" ] && echo "PROFILE_ID=$PROFILE_ID"
    exit 0
fi

# --- mlx_lm.server branch (dispatch-only tiers, e.g. Llama-4 which vllm-mlx misroutes) --------
if [ "$SERVE" = "mlx_lm" ]; then
    echo "🚀 Launching $MODEL_NAME via mlx_lm.server on free port $TARGET_PORT..."
    nohup "$LA_VENV/python" -m mlx_lm server --model "$MODEL_DIR" --host 127.0.0.1 --port "$TARGET_PORT" \
        --max-tokens 4096 > "$LOG_FILE" 2>&1 &
    wait_ready "$TARGET_PORT" "$LOG_FILE" "$MODEL_NAME" "$!" "$MODEL_DIR"
    echo "ℹ️  $MODEL_NAME model id = $MODEL_DIR  (use as the 'model' field when dispatching)"
    _preflight_warmup "$TARGET_PORT" "$MODEL_DIR"
    echo "DISPATCH_MODEL=$MODEL_DIR"
    echo "SUCCESS_PORT=$TARGET_PORT"; exit 0
fi

# --- llama.cpp / GGUF: recognised vocabulary, deliberately NOT launched from here -------------
if [ "$SERVE" = "llama_cpp" ]; then
    echo "❌ $MODEL_NAME is registered serve=llama_cpp (GGUF). This script launches MLX backends only."
    echo "   Start it with llama-server yourself, e.g.:"
    echo "     llama-server -m $MODEL_DIR/<model>.gguf --port 8080 -c 32768"
    echo "   llama.cpp remains the backend for GGUF artifacts; rapid/vllm are for MLX artifacts."
    exit 3
fi

# --- vllm-mlx branch (LEGACY lane — rapid is the default; see config-lib's backend vocabulary) --
TMP_CONFIG="$CONFIG_DIR/vllm_config_${TARGET_PORT}.yaml"
{
  echo "manager:"
  echo "  memory_budget_gb: $LA_MEMORY_BUDGET_GB"
  echo "models:"
  for _id in $(printf '%s' "$SPOOF_NAME" | tr ',' ' ') "$MODEL_NAME"; do
    [ -n "$_id" ] || continue
    echo "  - name: \"$_id\""
    echo "    path: \"$MODEL_DIR\""
    echo "    max_model_len: $LA_MAX_MODEL_LEN"
    echo "    kv_cache_quantization_level: 4"
  done
} > "$TMP_CONFIG"

EXTRA_ARGS="--enable-auto-tool-choice --tool-call-parser $TOOLP --timeout $LA_SERVER_TIMEOUT_S"
[ -n "$REASONP" ] && EXTRA_ARGS="$EXTRA_ARGS --reasoning-parser $REASONP --default-temperature 0.6 --default-top-p 0.95"
export VLLM_MLX_ENABLE_THINKING="${VLLM_MLX_ENABLE_THINKING:-$THINK}"

echo "🚀 Launching $MODEL_NAME on free port $TARGET_PORT  (🧠 thinking: $VLLM_MLX_ENABLE_THINKING)..."
nohup "$LA_VENV/vllm-mlx" serve --models-config "$TMP_CONFIG" --port "$TARGET_PORT" "${EXTRA_ARGS[@]}" > "$LOG_FILE" 2>&1 &
wait_ready "$TARGET_PORT" "$LOG_FILE" "$MODEL_NAME" "$!" "$SPOOF_PRIMARY"
tail -n 8 "$LOG_FILE"
_preflight_warmup "$TARGET_PORT" "$SPOOF_PRIMARY"
echo "DISPATCH_MODEL=$MODEL_NAME"
echo "SUCCESS_PORT=$TARGET_PORT"
[ "$USE_PROFILE" = "true" ] && echo "PROFILE_ID=$PROFILE_ID"
