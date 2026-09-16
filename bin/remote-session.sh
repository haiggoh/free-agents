#!/usr/bin/env bash
# remote-session.sh — launch a full Claude Code session against a REMOTE
# cloud API (Gemini / Groq / NVIDIA / …) instead of the paid Anthropic gateway.
#
# Why a proxy: Claude Code speaks Anthropic's /v1/messages; these routes use
# provider APIs through LiteLLM (OpenAI /chat/completions or native adapters).
# We run LiteLLM as a translating proxy in front of the chosen provider and point
# Claude Code at it, exactly as a local MLX session points at Rapid-MLX.
#
# Usage:
#   remote-session.sh                      # numbered picker of remote agents
#   remote-session.sh <alias>              # launch that remote agent
#   remote-session.sh --list               # print the remote roster and key status
#   remote-session.sh --verify <alias>     # check credential + model id live, launch nothing
#   remote-session.sh --dry-run <alias>    # print the plan and the env, start nothing
#   remote-session.sh --stop               # stop any proxy this script started
#   remote-session.sh --include-trials     # also offer trial-tier (non-free) agents
#   remote-session.sh --models <alias>     # list catalog IDs, pricing and tool metadata (GET only)
#   remote-session.sh <alias> --remote-model <id> # explicitly choose the upstream model
#
# Environment:
#   LA_API_KEYS_DIR             credential dir (default ~/.api_keys)
#   LA_REMOTE_PROXY_PORT_MIN/MAX  proxy port scan range (default 4141-4151)
#   LA_REMOTE_MAX_OUTPUT_TOKENS   CLAUDE_CODE_MAX_OUTPUT_TOKENS (default 8192)
#   LA_REMOTE_KEEP_PROXY=1        leave the proxy running after the session exits
#
# Cost: provider quota and billing apply; see the selected tier. This session
# does not use the Anthropic gateway. Catalog checks consume no generation tokens.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROSTER="$REPO_ROOT/config/remote-agents.sh"
KEYS="$SCRIPT_DIR/remote-keys.sh"
RUNDIR="${TMPDIR:-/tmp}/local-agents-remote"

# Corporate wifi runs a TLS-inspecting proxy, so Python's certifi bundle rejects every
# HTTPS call while the macOS Keychain accepts it. The repo already ships the fix as a
# PYTHONPATH shim (truststore.inject_into_ssl + IPv4 forcing) used by the model
# downloader — reuse it here rather than inventing a second mechanism, because LiteLLM
# is Python and makes the upstream provider calls. See memory corporate-wifi-python-ssl-mitm.
LA_TRUST_SHIM="$REPO_ROOT/install/hf-ipv4"
if [[ -f "$LA_TRUST_SHIM/sitecustomize.py" ]]; then
    export PYTHONPATH="${LA_TRUST_SHIM}${PYTHONPATH:+:$PYTHONPATH}"
fi

# shellcheck source=/dev/null
[[ -r "$ROSTER" ]] || { echo "remote-session: missing roster: $ROSTER" >&2; exit 2; }
source "$ROSTER"

MAX_OUT="${LA_REMOTE_MAX_OUTPUT_TOKENS:-8192}"
INCLUDE_TRIALS=0
DRY_RUN=0
MODE="launch"
ALIAS=""
REMOTE_MODEL=""

usage() {
    sed -n '2,/^set -uo pipefail/{ /^set -uo pipefail/d; s/^# \{0,1\}//; p; }' "$0"
    echo ""
    echo "Additional options:"
    echo "  -i, --install-keys    Install / set up remote API keys"
    echo "  -a, --auto-mode       Toggle auto-mode: blind-trust → classifier → off"
    echo "  -t, --telemetry       Toggle telemetry: OFF — no nonessential outbound traffic"
    echo "  -c, --choose-effort   Choose effort level for the selected model"
}

_valid_model() {
    [[ "$1" =~ ^[a-zA-Z0-9@][a-zA-Z0-9_./:@+-]*$ && "$1" != SELECT ]] || {
        echo 'remote-session: provide a model ID using --remote-model (letters, digits, _ . / : @ + - only).' >&2
        return 2
    }
}

_available() {
    [[ "$1" != github ]] || {
        echo 'remote-session: GitHub Models is retired (official docs; catalog HTTP 410 checked 2026-09-14). Select another provider directly; no generation or automatic fallback attempted. https://docs.github.com/en/github-models' >&2
        return 2
    }
}

# Generic OpenAI-compatible routes, verified against official provider docs.
# Always set api_base explicitly: no OpenAI endpoint or credential fallthrough.
_openai_base() {
    case "$1" in
        mistral) echo 'https://api.mistral.ai/v1' ;;
        zai) echo 'https://api.z.ai/api/paas/v4' ;;
        siliconflow) echo 'https://api.siliconflow.com/v1' ;;
        llm7) echo 'https://api.llm7.io/v1' ;;
        kilo) echo 'https://api.kilo.ai/api/gateway' ;;
        vercel) echo 'https://ai-gateway.vercel.sh/v1' ;;
        sambanova) echo 'https://api.sambanova.ai/v1' ;;
        modelscope) echo 'https://api-inference.modelscope.cn/v1' ;;
        *) return 2 ;;
    esac
}

_clear_provider_env() {
    # The user's shell may still globally export keys. Keep only the selected
    # provider in the proxy; no provider secrets are needed by the Claude child.
    local name
    for name in GEMINI_API_KEY GROQ_API_KEY OPENROUTER_API_KEY CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID GITHUB_MODELS_TOKEN CEREBRAS_API_KEY NVIDIA_API_KEY MISTRAL_API_KEY ZAI_API_KEY SILICONFLOW_API_KEY LLM7_API_KEY KILO_API_KEY AI_GATEWAY_API_KEY SAMBANOVA_API_KEY MODELSCOPE_API_KEY OPENAI_API_KEY; do
        unset "$name"
    done
}

# ---- roster helpers ---------------------------------------------------------
_field() { # _field <entry> <n>
    printf '%s' "$1" | cut -d'|' -f"$2"
}
_entry_for() { # _entry_for <alias>  -> the roster line, or empty
    local a="$1" e
    if [[ "$a" == github-models || "$a" == github ]]; then
        _available github
        return 2
    fi
    if [[ "$a" == "cerebras-legacy" ]]; then
        echo "remote-session: cerebras-legacy now selects cerebras-oss120; the old Llama id is unavailable." >&2
        a="cerebras-oss120"
    fi
    for e in "${LA_REMOTE_AGENTS[@]}"; do
        [[ "$(_field "$e" 1)" == "$a" ]] && { printf '%s' "$e"; return 0; }
    done
    return 1
}
_visible() { # tier filter: hide trials unless asked
    local tier="$1"
    [[ "$tier" != "trial" ]] || [[ $INCLUDE_TRIALS -eq 1 ]]
}

print_list() {
    printf '\n\033[1m☁️  REMOTE cloud-API agents\033[0m  (provider quotas/billing apply; catalog listing is not a tool-use test)\n\n'
    printf '  %-3s %-25s %-37s %-15s %s\n' '#' 'ALIAS' 'DISPLAY' 'TIER' 'KEY'
    local i=0 e alias prov model disp tier keystate
    for e in "${LA_REMOTE_AGENTS[@]}"; do
        alias="$(_field "$e" 1)"; prov="$(_field "$e" 2)"; model="$(_field "$e" 3)"
        disp="$(_field "$e" 4)"; tier="$(_field "$e" 5)"
        _visible "$tier" || continue
        i=$((i+1))
        if "$KEYS" --check "$prov" >/dev/null 2>&1; then keystate="✓ $prov"; else keystate="✗ $prov (no key)"; fi
        printf '  %-3s %-25s %-37s %-15s %s\n' "$i" "$alias" "$disp" "$tier" "$keystate"
    done
    [[ $INCLUDE_TRIALS -eq 0 ]] && printf '\n  (trial-tier agents hidden — pass --include-trials to show them)\n'
    printf '\n  Local MLX models are a different list: use `csl` / launch-claude-agent.sh.\n\n'
}

pick_alias() { # interactive numbered picker -> echoes the chosen alias
    local -a choices=() e alias tier
    for e in "${LA_REMOTE_AGENTS[@]}"; do
        tier="$(_field "$e" 5)"; _visible "$tier" || continue
        choices+=("$(_field "$e" 1)")
    done
    print_list >&2
    local n
    read -r -p "  Select a remote agent [1-${#choices[@]}] (q to quit): " n >&2 || return 1
    [[ "$n" == "q" || -z "$n" ]] && return 1
    [[ "$n" =~ ^[0-9]+$ ]] && (( n >= 1 && n <= ${#choices[@]} )) || {
        echo "remote-session: not a valid choice: $n" >&2; return 1; }
    printf '%s' "${choices[$((n-1))]}"
}

# ---- live verification ------------------------------------------------------
# On-demand catalog GET only: public catalogs do not validate credentials,
# and no catalog proves generation access or tool use.
_cloudflare_base() {
    local line account=""
    while IFS= read -r line; do
        [[ "$line" == CLOUDFLARE_ACCOUNT_ID=* ]] && account="${line#*=}"
    done < <("$KEYS" --env cloudflare)
    [[ "$account" =~ ^[a-fA-F0-9]{32}$ ]] || {
        echo 'remote-session: cloudflare-account-id must contain the 32-character account ID.' >&2
        return 1
    }
    printf 'https://api.cloudflare.com/client/v4/accounts/%s/ai/v1' "$account"
}

_catalog_request() {
    local prov="$1" envline key base
    _available "$prov" || return 2
    case "$prov" in
        gemini)     base="https://generativelanguage.googleapis.com/v1beta/openai/models" ;;
        groq)       base="https://api.groq.com/openai/v1/models" ;;
        nvidia)     base="https://integrate.api.nvidia.com/v1/models" ;;
        openrouter) base="https://openrouter.ai/api/v1/models" ;;
        cerebras)   base="https://api.cerebras.ai/v1/models" ;;
        cloudflare) base="$(_cloudflare_base)" || return 1
                    base="${base%/v1}/models/search?per_page=100" ;;
        mistral|llm7|kilo|vercel|sambanova) base="$(_openai_base "$prov")/models" ;;
        siliconflow) base="$(_openai_base "$prov")/models?type=text" ;;
        zai) echo 'remote-session: no documented ZAI catalog route; use --remote-model ID from https://docs.z.ai/guides/overview . This route uses general API billing, not the Coding Plan.' >&2; return 2 ;;
        modelscope) echo 'remote-session: no documented ModelScope catalog route; use --remote-model ID from the API-Inference model page: https://modelscope.cn/docs/model-service/API-Inference/intro' >&2; return 2 ;;
        *) echo "remote-session: no documented catalog route for $prov; choose an API model from its provider docs with --remote-model ID. Catalog verification unavailable." >&2; return 2 ;;
    esac
    envline="$("$KEYS" --env "$prov")" || return 1
    envline="${envline%%$'\n'*}"
    key="${envline#*=}"
    # curl, not python: it uses the system trust store, which survives the
    # corporate TLS-inspecting proxy that breaks python's default bundle.
    # Keep the credential out of argv; escape curl-config quotes/backslashes.
    key="${key//\\/\\\\}"; key="${key//\"/\\\"}"
    printf 'header = "Authorization: Bearer %s"\n' "$key" |
        curl --config - --fail --silent --max-time 25 "$base" || {
        printf '  model check : ? catalog request failed (network, authorization, or service error)\n' >&2; return 1; }
}

catalog_models() { # tab-separated model ID and metadata; no default selection
    local body
    body="$(_catalog_request "$1")" || return $?
    printf '%s' "$body" | python3 -c '
import json,re,sys
try:
    payload=json.load(sys.stdin)
    rows=payload.get("data",payload.get("result")) if isinstance(payload,dict) else payload
    if isinstance(payload,dict) and payload.get("success") is False: raise ValueError()
    if not isinstance(rows,list): raise ValueError()
    result=[]
    for row in rows:
        if not isinstance(row,dict): raise ValueError()
        ident=row.get("name",row.get("id")) if isinstance(payload,dict) and "result" in payload else row.get("id")
        if not isinstance(ident,str): raise ValueError()
        if ident.startswith("models/"): ident=ident.split("/",1)[1]
        if not re.fullmatch(r"[a-zA-Z0-9@][a-zA-Z0-9_./:@+-]*",ident): raise ValueError()
        caps=row.get("capabilities") or {}
        if not isinstance(caps,dict): caps={}
        if caps.get("completion_chat") is False or caps.get("function_calling") is False or row.get("tools_calling") is False: continue
        if row.get("model_type",row.get("type", "chat")) not in ("chat", "language", "text", "model", "chat-completion"): continue
        params=row.get("supported_parameters")
        tools=caps.get("function_calling",row.get("tools_calling",("tools" in params) if isinstance(params,list) else "unknown"))
        if tools is False: continue
        # JSON serialization escapes terminal control characters in metadata.
        metadata=json.dumps({"pricing":row.get("pricing","unknown"),"tier":row.get("tier","unknown"),"tools":tools},ensure_ascii=True)
        result.append(ident+"\t"+metadata)
    if not result: raise ValueError()
except (ValueError,TypeError,AttributeError):
    print("remote-session: empty, malformed, error, or no chat/tool candidates in catalog.",file=sys.stderr); sys.exit(1)
print("\n".join(result))
'
}

choose_model() {
    local prov="$1" rows line n i=0
    local -a models=()
    if [[ "$prov" == zai || "$prov" == modelscope ]]; then
        if [[ "$prov" == zai ]]; then
            echo 'Choose an API model from https://docs.z.ai/guides/overview . This route uses general API billing, not the Coding Plan.' >&2
        else
            echo 'Choose an API-Inference model from https://modelscope.cn/docs/model-service/API-Inference/intro .' >&2
        fi
        echo 'Model access, tool sessions and account billing are untested. No default model is selected.' >&2
        read -r -p 'Enter the exact model ID (q to cancel): ' n >&2 || return 2
        [[ "$n" != q ]] || return 2
        _valid_model "$n" || return 2
        printf '%s' "$n"
        return 0
    fi
    rows="$(catalog_models "$prov")" || return $?
    echo 'Catalog candidates only; tool sessions, account access and billing untested. Pricing units are provider-specific. No model is selected by default.' >&2
    while IFS= read -r line; do
        models+=("${line%%$'\t'*}")
        i=$((i+1)); printf '%3s  %s\n' "$i" "$line" >&2
    done <<< "$rows"
    read -r -p "Choose a model [1-${#models[@]}] (q to cancel): " n >&2 || return 2
    [[ "$n" =~ ^[0-9]+$ && ${#n} -lt 6 ]] || return 2
    n=$((10#$n))
    (( n >= 1 && n <= ${#models[@]} )) || return 2
    printf '%s' "${models[$((n-1))]}"
}

verify_alias() { # verify_alias <alias>  (0 = catalog-listed)
    local entry alias prov model tier body
    entry="$(_entry_for "$1")" || { echo "remote-session: unknown alias: $1" >&2; return 2; }
    alias="$(_field "$entry" 1)"; prov="$(_field "$entry" 2)"
    _available "$prov" || return 2
    model="${REMOTE_MODEL:-$(_field "$entry" 3)}"; tier="$(_field "$entry" 5)"
    if [[ -n "$REMOTE_MODEL" && "$REMOTE_MODEL" != "$(_field "$entry" 3)" && "$tier" != trial ]]; then
        tier=unknown
    fi
    _valid_model "$model" || return 2
    printf '  provider   : %s\n  model      : %s\n  tier       : %s\n' "$prov" "$model" "$tier"
    body="$(_catalog_request "$prov")" || return $?
    printf '%s' "$body" | MODEL_ID="$model" python3 -c '
import json,os,sys
want=os.environ["MODEL_ID"]
try:
    payload=json.load(sys.stdin)
    rows=payload.get("data",payload.get("result"))
    if payload.get("success") is False or not isinstance(rows,list): raise ValueError()
    ids=[m.get("name",m.get("id")) if "result" in payload else m.get("id") for m in rows]
    if not all(isinstance(i,str) for i in ids): raise ValueError()
except Exception: print("  model check : ? unparseable or error catalogue response"); sys.exit(1)
ids=[i.split("/",1)[1] if i.startswith("models/") else i for i in ids]
if want in ids: print("  model check : \033[32m✓ %s is catalog-listed (%d models); generation and quota untested\033[0m" % (want,len(ids)))
else:
    print("  model check : \033[31m✗ %s NOT in this catalogue response\033[0m (%d models)" % (want,len(ids)))
    near=[i for i in ids if want.split("/")[-1].split("-")[0] in i][:5]
    if near: print("                closest live ids: " + ", ".join(near))
    sys.exit(3)
'
}

# ---- proxy management -------------------------------------------------------
_prepare_runtime_dir() {
    if [[ -L "$RUNDIR" || ( -e "$RUNDIR" && ( ! -d "$RUNDIR" || ! -O "$RUNDIR" ) ) ]]; then
        echo "remote-session: refusing unsafe runtime directory: $RUNDIR" >&2
        return 1
    fi
    mkdir -p "$RUNDIR" && chmod 700 "$RUNDIR"
}

_prepare_runtime_file() {
    local path="$1"
    if [[ -L "$path" || ( -e "$path" && ( ! -f "$path" || ! -O "$path" ) ) ]]; then
        echo "remote-session: refusing unsafe runtime file: $path" >&2
        return 1
    fi
    # Restrict existing files before truncating; umask only protects new files.
    if [[ -e "$path" ]]; then
        chmod 600 "$path" || return 1
    fi
    ( umask 077; : > "$path" ) && chmod 600 "$path"
}

free_port() {
    local p
    for ((p=LA_REMOTE_PROXY_PORT_MIN; p<=LA_REMOTE_PROXY_PORT_MAX; p++)); do
        if ! lsof -ti tcp:"$p" >/dev/null 2>&1; then echo "$p"; return 0; fi
    done
    echo "remote-session: no free port in ${LA_REMOTE_PROXY_PORT_MIN}-${LA_REMOTE_PROXY_PORT_MAX}" >&2
    return 1
}

# A pid can be REUSED by an unrelated process after ours dies, so never kill on the
# strength of a stale pid file alone: confirm the process really is the litellm proxy
# started from OUR config path before signalling it.
_is_our_proxy() { # _is_our_proxy <pid> <port>
    local pid="${1:-}" port="${2:-}" cmd
    [[ -n "$pid" && -n "$port" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    cmd="$(ps -o command= -p "$pid" 2>/dev/null)" || return 1
    # Match either the wrapper or a bare litellm (older running proxies predate the
    # wrapper). The config path is what actually identifies OUR proxy on THIS port.
    [[ ( "$cmd" == *litellm* || "$cmd" == *litellm-with-trust* ) && "$cmd" == *"proxy-$port.yaml"* ]]
}

stop_one_proxy() { # stop_one_proxy <port>
    local port="${1:-}" pf pid
    [[ -n "$port" ]] || return 0
    pf="$RUNDIR/proxy-$port.pid"
    pid="$(cat "$pf" 2>/dev/null)"
    if _is_our_proxy "$pid" "$port"; then
        kill "$pid" 2>/dev/null && echo "   proxy    : stopped (pid $pid, port $port)"
    fi
    rm -f "$pf"
    return 0
}

stop_proxy() {
    local killed=0 pf port pid
    # A literal unmatched glob would be iterated as a filename, so nullglob guards the
    # no-proxy-running case instead of relying on the -e test alone.
    shopt -s nullglob
    for pf in "$RUNDIR"/proxy-*.pid; do
        port="$(basename "$pf")"; port="${port#proxy-}"; port="${port%.pid}"
        pid="$(cat "$pf" 2>/dev/null)"
        if _is_our_proxy "$pid" "$port"; then
            kill "$pid" 2>/dev/null && killed=$((killed+1))
            echo "remote-session: stopped proxy pid $pid (port $port)"
        fi
        rm -f "$pf"
    done
    shopt -u nullglob
    [[ $killed -eq 0 ]] && echo "remote-session: no proxy of ours was running"
    return 0
}

# LiteLLM maps each spoofed Claude id onto the chosen remote model, so Claude
# Code can ask for "claude-opus-5" and get the free provider underneath.
write_proxy_config() { # write_proxy_config <cfgpath> <provider> <model> <thinking>
    local cfg="$1" prov="$2" model="$3" thinking="$4"
    _available "$prov" || return 2
    _valid_model "$model" || return 2
    local litellm_model think_line="" api_base=""
    case "$prov" in
        gemini)     litellm_model="gemini/$model" ;;
        groq)       litellm_model="groq/$model" ;;
        nvidia)     litellm_model="nvidia_nim/$model" ;;
        openrouter) litellm_model="openrouter/$model" ;;
        cerebras)   litellm_model="cerebras/$model" ;;
        cloudflare) litellm_model="openai/$model"; api_base="$(_cloudflare_base)" || return 1 ;;
        mistral|zai|siliconflow|llm7|kilo|vercel|sambanova|modelscope)
                    litellm_model="openai/$model"; api_base="$(_openai_base "$prov")" || return 1 ;;
        *)          echo "remote-session: no LiteLLM prefix for provider $prov" >&2; return 1 ;;
    esac
    # Gemini 3.x spends its whole token budget on thinking unless told not to;
    # that truncated real replies during bring-up, so default it OFF.
    [[ "$prov" == "gemini" && "$thinking" != "true" ]] && \
        think_line='      thinking: {"type": "disabled"}'
    # NVIDIA NIM reasoning models (Nemotron) put their reasoning in
    # `reasoning_content`, which is NOT an Anthropic thinking block -- the
    # translation layer then dies with "Content block is not a thinking block"
    # and takes the whole session's endpoint with it. Turn reasoning off at the
    # backend unless the caller explicitly asked for thinking.
    [[ "$prov" == "nvidia" && "$thinking" != "true" ]] && \
        think_line='      chat_template_kwargs:
        enable_thinking: false'

    local key_env
    key_env="$("$KEYS" --names "$prov")" || return 1
    key_env="${key_env%%$'\n'*}"

    _prepare_runtime_file "$cfg" || return 1
    echo "model_list:" >> "$cfg"
    local spoof
    IFS=',' read -r -a _spoofs <<< "$LA_REMOTE_SPOOF_IDS"
    for spoof in "${_spoofs[@]}"; do
        {
            echo "  - model_name: $spoof"
            echo "    litellm_params:"
            echo "      model: $litellm_model"
            echo "      api_key: os.environ/$key_env"
            [[ -n "$api_base" ]] && echo "      api_base: $api_base"
            [[ -n "$think_line" ]] && echo "$think_line"
        } >> "$cfg"
    done
    cat >> "$cfg" <<'YAML'
litellm_settings:
  drop_params: true
  telemetry: false
general_settings:
  master_key: sk-local-agents-remote
YAML
}

start_proxy() { # start_proxy <provider> <model> <thinking> -> echoes port
    local prov="$1" model="$2" thinking="$3"
    umask 077
    command -v litellm >/dev/null 2>&1 || {
        echo "remote-session: litellm not found. Install with: pipx install litellm[proxy]" >&2; return 1; }
    # RESOLVE THE INTERPRETER BEHIND THE CONSOLE SCRIPT, and start the proxy through our
    # own wrapper instead of the script itself. The pipx console script's shebang carries
    # `-E`, which makes Python IGNORE PYTHONPATH -- so the Keychain-trust shim this file
    # exports above never reached the proxy, and on a TLS-inspecting corporate network every
    # upstream HTTPS call failed with "self-signed certificate in certificate chain" while
    # LiteLLM reported it as an HTTP 500 (reads like a provider outage; is not).
    # Re-entering the SAME interpreter without -E fixes it without touching ~/.local/pipx,
    # without disabling verification, and without any global CA change.
    # ESCAPE HATCH, deliberately explicit and loud. A caller can supply a complete proxy
    # command (a differently-installed litellm, or a test stub) via LA_LITELLM_CMD. It
    # BYPASSES the trust wrapper, so it announces itself: a silent bypass is exactly how the
    # -E bug stayed hidden, and this must not become a second quiet path to certifi-only TLS.
    local port cfg log pidf
    _prepare_runtime_dir || return 1
    port="$(free_port)" || return 1
    cfg="$RUNDIR/proxy-$port.yaml"; log="$RUNDIR/proxy-$port.log"; pidf="$RUNDIR/proxy-$port.pid"
    write_proxy_config "$cfg" "$prov" "$model" "$thinking" || return 1
    _prepare_runtime_file "$log" || return 1
    _prepare_runtime_file "$pidf" || return 1
    if [[ -n "${LA_LITELLM_CMD:-}" ]]; then
        # shellcheck disable=SC2206
        local -a _cmd=(${LA_LITELLM_CMD})
        command -v "${_cmd[0]}" >/dev/null 2>&1 || [[ -x "${_cmd[0]}" ]] || {
            echo "remote-session: LA_LITELLM_CMD is not executable: ${_cmd[0]}" >&2; return 1; }
        echo "remote-session: NOTE using LA_LITELLM_CMD (${_cmd[0]}) — the OS-trust wrapper is BYPASSED;" >&2
        echo "  on a TLS-inspecting network upstream HTTPS may fail with a certificate error." >&2
        local envassign_o line_o
        envassign_o="$("$KEYS" --env "$prov")" || return 1
        ( _clear_provider_env
          while IFS= read -r line_o; do export "$line_o"; done <<< "$envassign_o"
          exec "${_cmd[@]}" --config "$cfg" --port "$port" ) > "$log" 2>&1 &
        echo $! > "$pidf"
        local j
        for j in $(seq 1 60); do
            if curl -s -m 2 "http://127.0.0.1:$port/health/liveliness" >/dev/null 2>&1; then
                echo "$port"; return 0
            fi
            kill -0 "$(cat "$pidf")" 2>/dev/null || { echo "remote-session: proxy died during startup; inspect the private log locally: $log" >&2; return 1; }
            sleep 1
        done
        echo "remote-session: proxy did not become ready in 60s; inspect the private log locally: $log" >&2
        return 1
    fi

    local litellm_py litellm_shebang
    litellm_shebang="$(head -1 "$(command -v litellm)" 2>/dev/null)"
    litellm_py="${litellm_shebang#\#!}"; litellm_py="${litellm_py%% *}"
    if [[ ! -x "$litellm_py" ]]; then
        echo "remote-session: could not resolve the litellm interpreter from: ${litellm_shebang:-<none>}" >&2
        echo "  reinstall with: pipx install 'litellm[proxy]'" >&2
        return 1
    fi
    local trust_wrapper="$SCRIPT_DIR/litellm-with-trust.py"
    if [[ ! -r "$trust_wrapper" ]]; then
        echo "remote-session: missing trust wrapper: $trust_wrapper" >&2
        return 1
    fi

    # Only THIS provider's credential enters the proxy environment.
    local envassign line
    envassign="$("$KEYS" --env "$prov")" || return 1
    ( _clear_provider_env
      while IFS= read -r line; do export "$line"; done <<< "$envassign"
      exec "$litellm_py" "$trust_wrapper" --config "$cfg" --port "$port" ) > "$log" 2>&1 &
    echo $! > "$pidf"

    # Bounded readiness wait with real diagnostics on failure.
    local i
    for i in $(seq 1 60); do
        if curl -s -m 2 "http://127.0.0.1:$port/health/liveliness" >/dev/null 2>&1; then
            echo "$port"; return 0
        fi
        kill -0 "$(cat "$pidf")" 2>/dev/null || { echo "remote-session: proxy died during startup; inspect the private log locally: $log" >&2; return 1; }
        sleep 1
    done
    echo "remote-session: proxy did not become ready in 60s; inspect the private log locally: $log" >&2
    return 1
}

# ---- argument parsing (BEFORE any work — --help must never launch anything) --
# Our own flags are consumed here; everything else is PASSED THROUGH to `claude`
# untouched (so `remote-session.sh gemini-flash -p "..." --allowedTools Read` works
# exactly like it does for a normal claude invocation). `--` forces the rest through.
PASSTHRU=()
AUTO_MODE_STATE=0  # 0=blind-trust, 1=classifier, 2=off
TELEMETRY_ENABLED=0  # 0=off (no nonessential traffic), 1=on (stock behavior)
SELECTED_EFFORT=""
EFFORT_CHOICE=""
PERMISSION_MODE="auto"   # resolved from AUTO_MODE_STATE below
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)        usage; exit 0 ;;
        --list)           MODE="list"; shift ;;
        --stop)           MODE="stop"; shift ;;
        --verify)         MODE="verify"; ALIAS="${2:-}"; shift 2 || shift ;;
        --models)         MODE="models"; ALIAS="${2:-}"; shift 2 || shift ;;
        --remote-model)   [[ $# -ge 2 ]] || { echo 'remote-session: --remote-model needs an ID' >&2; exit 2; }
                          REMOTE_MODEL="$2"; _valid_model "$REMOTE_MODEL" || exit 2; shift 2 ;;
        --dry-run)        DRY_RUN=1; shift ;;
        --include-trials) INCLUDE_TRIALS=1; shift ;;
        -i|--install-keys) MODE="install-keys"; shift ;;
        -w|--watcher)
            # Deliberately NOT a silent no-op. The remote watcher is deferred (it cannot
            # stream live reasoning, which is the part that would make it useful — see
            # ROADMAP / waypoint real-time-thinking-visibility). Accepting the flag and
            # doing nothing is exactly the dead-switch class of bug this release removes.
            echo "remote-session: -w/--watcher is not available for remote sessions yet" >&2
            echo "  (deferred: a watcher without live reasoning output is not worth the window;" >&2
            echo "   tracked in docs/ROADMAP.md). Local sessions still support it via csl." >&2
            exit 2 ;;
        -a|--auto-mode)   AUTO_MODE_STATE=$(( (AUTO_MODE_STATE + 1) % 3 )); shift ;;
        -t|--telemetry)   TELEMETRY_ENABLED=$((TELEMETRY_ENABLED ^ 1)); shift ;;
        -c|--choose-effort)
            if [[ -z "$ALIAS" ]]; then
                MODE="launch"
                SELECTED_EFFORT="choose-effort"
            else
                SELECTED_EFFORT="choose-effort"
            fi
            shift ;;
        --)               shift; PASSTHRU+=("$@"); break ;;
        -*)               if [[ -z "$ALIAS" ]]; then
                              echo "remote-session: unknown option: $1; use --help or put Claude options after an alias / --." >&2
                              exit 2
                          fi
                          PASSTHRU+=("$1"); shift ;;
        *)                if [[ -z "$ALIAS" && ${#PASSTHRU[@]} -eq 0 ]]; then ALIAS="$1"; else PASSTHRU+=("$1"); fi; shift ;;
    esac
done

if [[ "$ALIAS" == github-models || "$ALIAS" == github ]]; then
    _available github
    exit 2
fi

case "$MODE" in
    list) print_list; exit 0 ;;
    stop) stop_proxy; exit 0 ;;
    models)
        ENTRY="$(_entry_for "$ALIAS")" || { echo 'remote-session: --models needs a known alias' >&2; exit 2; }
        echo 'Catalog candidates only; listed prices/tier/tools are provider metadata. Generation, tool sessions and account billing untested.' >&2
        catalog_models "$(_field "$ENTRY" 2)"; exit $? ;;
    verify)
        [[ -n "$ALIAS" ]] || { echo "remote-session: --verify needs an alias" >&2; exit 2; }
        printf '\n\033[1mVerifying remote agent: %s\033[0m\n' "$ALIAS"
        verify_alias "$ALIAS"; rc=$?
        echo
        exit $rc ;;
esac

# ---- launch ----------------------------------------------------------------
if [[ -z "$ALIAS" ]]; then
    ALIAS="$(pick_alias)" || { echo "remote-session: nothing selected." >&2; exit 1; }
fi

# ---- interactive toggle resolution (launch mode) ---------------------------
# These map the parsed toggles onto what `claude` and the environment actually read.
# NOTE the history here: an earlier version of this file mapped the toggles inside a
# `_launch()` helper that was NEVER CALLED, so `-a` and `-t` silently did nothing while
# the picker advertised them. Anything that resolves a toggle must therefore sit on the
# live path (the exec near the bottom of this file), not in a helper.
if [[ "$MODE" == "launch" ]]; then
    # Auto-mode state -> the flag `claude` actually reads.
    #   0 = blind-trust  -> --permission-mode auto   (DEFAULT; no classifier in the loop)
    #   1 = classifier   -> --permission-mode auto   (+ classifier, see below)
    #   2 = off          -> --permission-mode acceptEdits
    # Unlike a LOCAL session, remote-session.sh execs `claude` directly rather than going
    # through launch-claude-agent.sh, so LA_AUTO_MODE/LA_BLIND_AUTO are NOT read by anyone
    # here. We must pass --permission-mode ourselves; exporting those vars alone was the
    # original bug. They are still exported for any child that inspects them.
    case "$AUTO_MODE_STATE" in
        0) PERMISSION_MODE="auto";        LA_AUTO_MODE=1; LA_BLIND_AUTO=1 ;;
        1) PERMISSION_MODE="auto";        LA_AUTO_MODE=1; LA_BLIND_AUTO=0 ;;
        2) PERMISSION_MODE="acceptEdits"; LA_AUTO_MODE=0; LA_BLIND_AUTO=0 ;;
    esac
    export LA_AUTO_MODE LA_BLIND_AUTO

    # The genuine classifier lane is not wired for remote yet. Say so rather than
    # silently behaving like blind-trust, which is the failure mode this release fixes.
    if [[ "$AUTO_MODE_STATE" -eq 1 ]]; then
        echo "⚠️  auto-mode: classifier requested, but the remote classifier lane is not"
        echo "    implemented yet — running blind-trust (auto) for this session. See ROADMAP."
    fi

    # Telemetry must work in BOTH directions. The live path used to hardcode the
    # suppression, so `-t` could never restore stock behaviour.
    if [[ "$TELEMETRY_ENABLED" -eq 1 ]]; then
        unset CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
        LA_REMOTE_TELEMETRY=1
    else
        export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
        LA_REMOTE_TELEMETRY=0
    fi
    export LA_REMOTE_TELEMETRY
fi

# Helper function for picking from a list (similar to CSL)
_pick_from() {
    # $1=prompt  $2=default index (1-based)  rest=items -> echoes chosen 1-based index
    local prompt="$1" def="$2"; shift 2
    local n=$#
    local i=1
    for it in "$@"; do printf "  %d) %s\n" "$i" "$it" >&2; i=$((i+1)); done
    local c; printf "%s [%s]: " "$prompt" "$def" >&2; read -r c; c="${c:-$def}"
    if ! [[ "$c" =~ ^[0-9]+$ ]] || [ "$c" -lt 1 ] || [ "$c" -gt "$n" ]; then echo "0"; return; fi
    echo "$c"
}


# Handle effort selection if requested
if [[ "$SELECTED_EFFORT" == "choose-effort" ]]; then
    # Map effort levels to Claude's choices
    efforts=("min" "low" "medium" "high")
    effort_index=$(_pick_from "Select effort level" "2" "${efforts[@]}")
    if [[ "$effort_index" == "0" ]]; then
        echo "remote-session: effort selection cancelled." >&2
        exit 1
    fi
    EFFORT_CHOICE="${efforts[$((effort_index-1))]}"
fi

# Special mode handlers
if [[ "$MODE" == "install-keys" ]]; then
    echo "Installing remote API keys..."
    exec python3 "$REPO_ROOT/install/setup-api-keys.py" "$@"
    exit 0
fi


ENTRY="$(_entry_for "$ALIAS")" || {
    echo "remote-session: unknown remote alias: $ALIAS" >&2
    print_list >&2
    exit 2
}
PROV="$(_field "$ENTRY" 2)"; MODEL="$(_field "$ENTRY" 3)"
_available "$PROV" || exit 2
if [[ -n "$REMOTE_MODEL" ]]; then
    MODEL="$REMOTE_MODEL"
elif [[ "$MODEL" == SELECT ]]; then
    if [[ $DRY_RUN -eq 1 || ! -t 0 ]]; then
        echo "remote-session: '$ALIAS' needs --remote-model ID; use --models $ALIAS to discover catalog candidates." >&2
        exit 2
    fi
    MODEL="$(choose_model "$PROV")" || exit $?
fi
_valid_model "$MODEL" || exit 2
DISP="$(_field "$ENTRY" 4)"; TIER="$(_field "$ENTRY" 5)"
# An explicit override may be paid even when the original alias has a free pin.
if [[ -n "$REMOTE_MODEL" && "$REMOTE_MODEL" != "$(_field "$ENTRY" 3)" && "$TIER" != trial ]]; then
    TIER=unknown
fi
THINKING=false
[[ "$ALIAS" == *-thinking ]] && THINKING=true

if [[ "$TIER" == "trial" && $INCLUDE_TRIALS -eq 0 ]]; then
    echo "remote-session: '$ALIAS' is TRIAL tier (not free). Re-run with --include-trials to use it." >&2
    exit 2
fi

"$KEYS" --check "$PROV" >/dev/null 2>&1 || {
    echo "remote-session: no credential for provider '$PROV'." >&2
    echo "  expected: ${LA_API_KEYS_DIR:-$HOME/.api_keys}/$PROV" >&2
    exit 1
}

case "$TIER" in
    renewing_free) COST_NOTE='renewing free allocation; account limits/billing still apply' ;;
    trial) COST_NOTE='trial/paid access explicitly selected; check provider balance' ;;
    *) COST_NOTE='account quota and billing unverified; do not assume free' ;;
esac

cat <<BANNER

╭──────────────────────────────────────────────────────────────╮
│  ☁️  REMOTE API SESSION — $MODEL ($DISP)                    │
╰──────────────────────────────────────────────────────────────╯
   provider : $PROV      tier: $TIER
   agent    : $ALIAS
   thinking : $THINKING
   cost     : $COST_NOTE
   privacy  : prompts and file contents LEAVE this machine → $PROV
BANNER

if [[ $DRY_RUN -eq 1 ]]; then
    echo "   DRY RUN  : would start a LiteLLM proxy and exec claude against it."
    echo "              nothing started, no network call made."
    # Print the RESOLVED toggle state and the flags claude would actually receive.
    # A dry run that stops before this point is how the dead-switch bugs stayed hidden:
    # the toggles parsed, printed nothing, and were never applied. Showing the resolved
    # values makes each switch verifiable by OUTCOME without launching anything.
    case "$AUTO_MODE_STATE" in
        0) _am_label="blind-trust (auto, no classifier)" ;;
        1) _am_label="classifier requested → falls back to auto (lane not implemented)" ;;
        2) _am_label="off (acceptEdits)" ;;
    esac
    echo
    echo "   resolved toggles:"
    echo "     auto-mode      : $_am_label"
    echo "     permission-mode: --permission-mode $PERMISSION_MODE"
    if [[ "${LA_REMOTE_TELEMETRY:-0}" -eq 1 ]]; then
        echo "     telemetry      : ON  (stock Claude Code reporting)"
    else
        echo "     telemetry      : OFF (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1)"
    fi
    echo "     effort         : ${EFFORT_CHOICE:-<provider default>}"
    echo
    exit 0
fi

_prepare_runtime_dir || exit 1
echo "   proxy    : starting LiteLLM (Anthropic /v1/messages → $PROV)…"
PORT="$(start_proxy "$PROV" "$MODEL" "$THINKING")" || {
    echo "remote-session: could not start the translating proxy." >&2; exit 1; }
echo "   proxy    : ready on http://127.0.0.1:$PORT"
echo



export ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT"   # NO /v1 — Claude Code appends /v1/messages
export ANTHROPIC_AUTH_TOKEN="sk-local-agents-remote"
export ANTHROPIC_API_KEY="sk-local-agents-remote"
export CLAUDE_CODE_MAX_OUTPUT_TOKENS="$MAX_OUT"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1    # no telemetry through a third party
export CLAUDE_IS_REMOTE_API="true"                   # distinct from CLAUDE_IS_LOCAL
export LA_REMOTE_AGENT="$ALIAS"
export LA_REMOTE_PROVIDER="$PROV"

# The remote models are NOT native Claude Code models: without an explicit briefing
# they default to shelling out (cat/sed instead of Read/Edit) and will hand-edit a
# plugin's private state file instead of using its documented CLI. The prompt file
# carries that briefing; placeholders are filled the same way the local launcher
# fills its own (config/local-agent-system-prompt.txt).
: "${LA_REMOTE_PROMPT_FILE:=$REPO_ROOT/config/remote-agent-system-prompt.txt}"
if [[ ! -r "$LA_REMOTE_PROMPT_FILE" ]]; then
    echo "remote-session: remote agent prompt file is not readable: $LA_REMOTE_PROMPT_FILE" >&2
    exit 1
fi
AGENT_PROMPT="$(cat "$LA_REMOTE_PROMPT_FILE")"
AGENT_PROMPT=${AGENT_PROMPT//__LA_REMOTE_MODEL__/$MODEL}
AGENT_PROMPT=${AGENT_PROMPT//__LA_REMOTE_DISPLAY__/$DISP}
AGENT_PROMPT=${AGENT_PROMPT//__LA_REMOTE_PROVIDER__/$PROV}
AGENT_PROMPT=${AGENT_PROMPT//__LA_REMOTE_PORT__/$PORT}
AGENT_PROMPT=${AGENT_PROMPT//__LA_REMOTE_PORT_MIN__/$LA_REMOTE_PROXY_PORT_MIN}
AGENT_PROMPT=${AGENT_PROMPT//__LA_REMOTE_PORT_MAX__/$LA_REMOTE_PROXY_PORT_MAX}
AGENT_PROMPT=${AGENT_PROMPT//__LA_REMOTE_SPOOF__/claude-opus-5}

# SOFT, OPTIONAL link to the brief-agents plugin: if its generated briefing index
# exists, point the remote model at it. A non-native model has none of the durable
# rules a normal session gets from CLAUDE.md/memory, so it is exactly the audience
# the index was written for. Deliberately a POINTER, not an inlined copy: the index
# grows, and pasting it would re-send several KB on every turn of a quota-limited
# free lane. Absent plugin = absent file = this block is skipped, so there is no
# hard dependency in either direction.
: "${LA_BRIEFING_INDEX:=$HOME/.claude/agent-briefing-index.md}"
if [[ -r "$LA_BRIEFING_INDEX" ]]; then
    AGENT_PROMPT="$AGENT_PROMPT Durable rules for this machine that you do NOT otherwise inherit are indexed at ${LA_BRIEFING_INDEX} — READ IT with the Read tool before your first consequential action (any edit, commit, install, or plugin invocation), and follow it. It is authoritative over your own assumptions about local conventions."
fi

# Point it at the real memory dir rather than letting it guess a path, same as local.
[[ -n "${LA_MEMORY_DIR:-}" ]] && AGENT_PROMPT="$AGENT_PROMPT Your Claude Code auto-memory lives at ${LA_MEMORY_DIR} — read from there, don't guess memory paths."

# Fail loudly if a placeholder survived: a literal __LA_...__ reaching the model is a
# silent briefing bug, and an unbriefed remote model is the failure this file prevents.
if [[ "$AGENT_PROMPT" == *__LA_* ]]; then
    echo "remote-session: unfilled placeholder(s) in the agent prompt:" >&2
    printf '%s\n' "$AGENT_PROMPT" | grep -o '__LA_[A-Z_]*__' | sort -u >&2
    exit 1
fi

# NOT `exec`: exec REPLACES this shell, so an EXIT trap set here could never fire and
# every session leaked its proxy (measured: 5 orphaned litellm processes holding ports
# 4141-4145, each run silently landing on the next free port). Run claude as a child,
# then tear down the proxy this run owns. The trap covers the signal paths too, so a
# Ctrl-C or a killed terminal does not leave the proxy behind either.
_teardown() {
    local rc=$?
    trap - EXIT INT TERM HUP
    if [[ "${LA_REMOTE_KEEP_PROXY:-0}" == "1" ]]; then
        echo
        echo "   proxy    : left running on port $PORT (LA_REMOTE_KEEP_PROXY=1)"
        echo "              stop it with: $0 --stop"
    else
        stop_one_proxy "$PORT"
    fi
    exit $rc
}
trap _teardown EXIT INT TERM HUP

# Build Claude command with effort selection if specified.
# NOT `local` — this is top-level scope. `local` outside a function makes bash print
# "local: can only be used in a function" and return 1, yet STILL assign the array, so the
# bug was invisible: the session launched correctly while emitting an error line.
claude_cmd=(claude --model claude-opus-5 --strict-mcp-config --mcp-config '{"mcpServers":{}}' --append-system-prompt "$AGENT_PROMPT")
claude_cmd+=(--permission-mode "$PERMISSION_MODE")
if [[ -n "$EFFORT_CHOICE" ]]; then
    claude_cmd+=(--effort "$EFFORT_CHOICE")
fi
if [[ ${#PASSTHRU[@]} -gt 0 ]]; then
    claude_cmd+=("${PASSTHRU[@]}")
fi

( _clear_provider_env
"${claude_cmd[@]}" )
