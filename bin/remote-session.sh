#!/usr/bin/env bash
# remote-session.sh — launch a full Claude Code session against a FREE REMOTE
# cloud API (Gemini / Groq / NVIDIA / …) instead of the paid Anthropic gateway.
#
# Why a proxy: none of the free providers speak Anthropic's /v1/messages — they
# are all OpenAI /chat/completions. Claude Code speaks ONLY /v1/messages. So we
# run LiteLLM as a translating proxy in front of the chosen provider and point
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
#
# Environment:
#   LA_API_KEYS_DIR             credential dir (default ~/.api_keys)
#   LA_REMOTE_PROXY_PORT_MIN/MAX  proxy port scan range (default 4141-4151)
#   LA_REMOTE_MAX_OUTPUT_TOKENS   CLAUDE_CODE_MAX_OUTPUT_TOKENS (default 8192)
#   LA_REMOTE_KEEP_PROXY=1        leave the proxy running after the session exits
#
# Cost: the provider lanes are free tiers. This session does NOT touch the
# Anthropic gateway, so it does not consume the daily paid budget.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROSTER="$REPO_ROOT/config/remote-agents.sh"
KEYS="$SCRIPT_DIR/remote-keys.sh"
RUNDIR="${TMPDIR:-/tmp}/local-agents-remote"
mkdir -p "$RUNDIR"

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

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; }

# ---- roster helpers ---------------------------------------------------------
_field() { # _field <entry> <n>
    printf '%s' "$1" | cut -d'|' -f"$2"
}
_entry_for() { # _entry_for <alias>  -> the roster line, or empty
    local a="$1" e
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
    printf '\n\033[1m☁️  REMOTE cloud-API agents\033[0m  (free tiers — separate from the local MLX models)\n\n'
    printf '  %-3s %-22s %-34s %-15s %s\n' '#' 'ALIAS' 'DISPLAY' 'TIER' 'KEY'
    local i=0 e alias prov model disp tier keystate
    for e in "${LA_REMOTE_AGENTS[@]}"; do
        alias="$(_field "$e" 1)"; prov="$(_field "$e" 2)"; model="$(_field "$e" 3)"
        disp="$(_field "$e" 4)"; tier="$(_field "$e" 5)"
        _visible "$tier" || continue
        i=$((i+1))
        if "$KEYS" --check "$prov" >/dev/null 2>&1; then keystate="✓ $prov"; else keystate="✗ $prov (no key)"; fi
        printf '  %-3s %-22s %-34s %-15s %s\n' "$i" "$alias" "$disp" "$tier" "$keystate"
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
# Confirm the credential works AND the pinned model id still exists. A retired
# id is the failure mode that bit us before, so this is checked before launch.
verify_alias() { # verify_alias <alias>  (0 = usable)
    local entry alias prov model tier
    entry="$(_entry_for "$1")" || { echo "remote-session: unknown alias: $1" >&2; return 2; }
    alias="$(_field "$entry" 1)"; prov="$(_field "$entry" 2)"
    model="$(_field "$entry" 3)"; tier="$(_field "$entry" 5)"

    printf '  provider   : %s\n  model      : %s\n  tier       : %s\n' "$prov" "$model" "$tier"
    if ! "$KEYS" --check "$prov" >/dev/null 2>&1; then
        printf '  credential : ✗ MISSING — expected %s/%s\n' "${LA_API_KEYS_DIR:-$HOME/.api_keys}" "$prov"
        return 1
    fi
    printf '  credential : ✓ present\n'

    local envline key base
    envline="$("$KEYS" --env "$prov" | head -1)" || return 1
    key="${envline#*=}"
    case "$prov" in
        gemini)     base="https://generativelanguage.googleapis.com/v1beta/openai/models" ;;
        groq)       base="https://api.groq.com/openai/v1/models" ;;
        nvidia)     base="https://integrate.api.nvidia.com/v1/models" ;;
        openrouter) base="https://openrouter.ai/api/v1/models" ;;
        cerebras)   base="https://api.cerebras.ai/v1/models" ;;
        *)          printf '  model check : (skipped — no catalogue endpoint known for %s)\n' "$prov"; return 0 ;;
    esac
    # curl, not python: it uses the system trust store, which survives the
    # corporate TLS-inspecting proxy that breaks python's default bundle.
    local body
    body="$(curl -s -m 25 "$base" -H "Authorization: Bearer $key")" || {
        printf '  model check : ? network unavailable (not a bad credential)\n'; return 0; }
    printf '%s' "$body" | MODEL_ID="$model" python3 -c '
import json,os,sys
want=os.environ["MODEL_ID"]
try: ids=[m["id"] for m in json.load(sys.stdin).get("data",[])]
except Exception: print("  model check : ? unparseable catalogue response"); sys.exit(0)
ids=[i.split("/",1)[1] if i.startswith("models/") else i for i in ids]
if want in ids: print("  model check : \033[32m✓ %s is served right now (%d models)\033[0m" % (want,len(ids)))
else:
    print("  model check : \033[31m✗ %s NOT in the catalogue\033[0m (%d models) — the id was retired" % (want,len(ids)))
    near=[i for i in ids if want.split("/")[-1].split("-")[0] in i][:5]
    if near: print("                closest live ids: " + ", ".join(near))
    sys.exit(3)
'
}

# ---- proxy management -------------------------------------------------------
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
    [[ "$cmd" == *litellm* && "$cmd" == *"proxy-$port.yaml"* ]]
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
    local litellm_model think_line=""
    case "$prov" in
        gemini)     litellm_model="gemini/$model" ;;
        groq)       litellm_model="groq/$model" ;;
        nvidia)     litellm_model="nvidia_nim/$model" ;;
        openrouter) litellm_model="openrouter/$model" ;;
        cerebras)   litellm_model="cerebras/$model" ;;
        *)          echo "remote-session: no LiteLLM prefix for provider $prov" >&2; return 1 ;;
    esac
    # Gemini 3.x spends its whole token budget on thinking unless told not to;
    # that truncated real replies during bring-up, so default it OFF.
    [[ "$prov" == "gemini" && "$thinking" != "true" ]] && \
        think_line='      thinking: {"type": "disabled"}'

    local key_env
    key_env="$("$KEYS" --names "$prov" | head -1)"

    : > "$cfg"; chmod 600 "$cfg"
    echo "model_list:" >> "$cfg"
    local spoof
    IFS=',' read -r -a _spoofs <<< "$LA_REMOTE_SPOOF_IDS"
    for spoof in "${_spoofs[@]}"; do
        {
            echo "  - model_name: $spoof"
            echo "    litellm_params:"
            echo "      model: $litellm_model"
            echo "      api_key: os.environ/$key_env"
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
    command -v litellm >/dev/null 2>&1 || {
        echo "remote-session: litellm not found. Install with: pipx install litellm[proxy]" >&2; return 1; }
    local port cfg log pidf
    port="$(free_port)" || return 1
    cfg="$RUNDIR/proxy-$port.yaml"; log="$RUNDIR/proxy-$port.log"; pidf="$RUNDIR/proxy-$port.pid"
    write_proxy_config "$cfg" "$prov" "$model" "$thinking" || return 1

    # Only THIS provider's credential enters the proxy environment.
    local envassign
    envassign="$("$KEYS" --env "$prov")" || return 1
    ( set -a; eval "$envassign"; set +a
      exec litellm --config "$cfg" --port "$port" ) > "$log" 2>&1 &
    echo $! > "$pidf"

    # Bounded readiness wait with real diagnostics on failure.
    local i
    for i in $(seq 1 60); do
        if curl -s -m 2 "http://127.0.0.1:$port/health/liveliness" >/dev/null 2>&1; then
            echo "$port"; return 0
        fi
        kill -0 "$(cat "$pidf")" 2>/dev/null || { echo "remote-session: proxy died during startup — last log lines:" >&2; tail -15 "$log" >&2; return 1; }
        sleep 1
    done
    echo "remote-session: proxy did not become ready in 60s — last log lines:" >&2
    tail -15 "$log" >&2
    return 1
}

# ---- argument parsing (BEFORE any work — --help must never launch anything) --
# Our own flags are consumed here; everything else is PASSED THROUGH to `claude`
# untouched (so `remote-session.sh gemini-flash -p "..." --allowedTools Read` works
# exactly like it does for a normal claude invocation). `--` forces the rest through.
PASSTHRU=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)        usage; exit 0 ;;
        --list)           MODE="list"; shift ;;
        --stop)           MODE="stop"; shift ;;
        --verify)         MODE="verify"; ALIAS="${2:-}"; shift 2 || shift ;;
        --dry-run)        DRY_RUN=1; shift ;;
        --include-trials) INCLUDE_TRIALS=1; shift ;;
        --)               shift; PASSTHRU+=("$@"); break ;;
        -*)               PASSTHRU+=("$1"); shift ;;
        *)                if [[ -z "$ALIAS" && ${#PASSTHRU[@]} -eq 0 ]]; then ALIAS="$1"; else PASSTHRU+=("$1"); fi; shift ;;
    esac
done

case "$MODE" in
    list) print_list; exit 0 ;;
    stop) stop_proxy; exit 0 ;;
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

ENTRY="$(_entry_for "$ALIAS")" || {
    echo "remote-session: unknown remote alias: $ALIAS" >&2
    print_list >&2
    exit 2
}
PROV="$(_field "$ENTRY" 2)"; MODEL="$(_field "$ENTRY" 3)"
DISP="$(_field "$ENTRY" 4)"; TIER="$(_field "$ENTRY" 5)"
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

cat <<BANNER

╭──────────────────────────────────────────────────────────────╮
│  ☁️  REMOTE CLOUD-API SESSION  —  not local, not the gateway  │
╰──────────────────────────────────────────────────────────────╯
   agent    : $ALIAS  ($DISP)
   provider : $PROV      tier: $TIER
   model    : $MODEL      thinking: $THINKING
   cost     : free tier — does NOT consume the Anthropic daily budget
   privacy  : prompts and file contents LEAVE this machine → $PROV
BANNER

if [[ $DRY_RUN -eq 1 ]]; then
    echo "   DRY RUN  : would start a LiteLLM proxy and exec claude against it."
    echo "              nothing started, no network call made."
    echo
    exit 0
fi

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

claude --model claude-opus-5 \
    --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
    --append-system-prompt "$AGENT_PROMPT" \
    "${PASSTHRU[@]+"${PASSTHRU[@]}"}"
