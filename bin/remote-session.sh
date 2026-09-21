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

[[ -r "$ROSTER" ]] || { echo "remote-session: missing roster: $ROSTER" >&2; exit 2; }
# shellcheck source=/dev/null
source "$ROSTER"
# Load shared emoji constants (single source of truth)
# shellcheck source=../config/emoji.sh
source "$SCRIPT_DIR/../config/emoji.sh"

MAX_OUT="${LA_REMOTE_MAX_OUTPUT_TOKENS:-8192}"
INCLUDE_TRIALS=0
DRY_RUN=0
MODE="launch"
ALIAS=""
REMOTE_MODEL=""
# Local-capable filter: 0=hidden (default), 1=shown.
LOCAL_CAPABLE_SHOWN=0
# When true, remote-session.sh runs as a submenu of csl and returns
# via a navigation token on stdout instead of exec'ing claude.
CSL_OWNER=0
# Blind-trust settings file (set when AUTO_MODE_STATE=0)
BLIND_TRUST_SETTINGS_FILE=""
# Whether the blind-trust settings file was user-provided (vs generated)
BLIND_TRUST_SETTINGS_USER_PROVIDED=0

usage() {
    sed -n '2,/^set -uo pipefail/{ /^set -uo pipefail/d; s/^# \{0,1\}//; p; }' "$0"
    echo ""
    echo "Additional options:"
    echo "  -i, --install-keys    🔑  Install / set up remote API keys"
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
# ---------------------------------------------------------------------------
# Box drawing — kept deliberately identical to bin/csl's copy so both lanes frame
# their menus the same way. Rows are padded by MEASURED DISPLAY WIDTH, not by
# character count, because an emoji is one character but two terminal columns and
# several of these emoji carry a U+FE0F variation selector that adds no width at
# all. The previous code guessed a width from digit counts (`52 - label_len`),
# which is why this header drifted out of alignment whenever a count changed.
# BOX_INNER matches csl's value; change both together.
# ---------------------------------------------------------------------------
BOX_INNER=62

_box() { # _box top|mid|bottom|row|center [text...]
  LC_ALL=en_US.UTF-8 python3 -c '
import sys, unicodedata

INNER = int(sys.argv[1])
kind = sys.argv[2]
rows = sys.argv[3:]


def width(text):
    total = 0
    for char in text:
        if char == "\ufe0f" or unicodedata.combining(char):
            continue
        if unicodedata.east_asian_width(char) in ("W", "F") or ord(char) >= 0x1F300:
            total += 2
        else:
            total += 1
    return total


def truncate(text):
    if width(text) <= INNER:
        return text
    out = ""
    for char in text:
        if width(out + char) > INNER - 1:
            break
        out += char
    return out + "\u2026"


if kind in ("top", "mid", "bottom"):
    left, right = {"top": "\u2554\u2557", "mid": "\u2560\u2563",
                   "bottom": "\u255a\u255d"}[kind]
    print(left + "\u2550" * INNER + right)
else:
    for text in rows:
        if kind == "center":
            text = " " * max(0, (INNER - width(text)) // 2) + text
        text = truncate(text)
        print("\u2551" + text + " " * max(0, INNER - width(text)) + "\u2551")
' "$BOX_INNER" "$@"
}

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

# _tier_label <tier> <provider> -> what the USER READS in a picker/listing.
#
# The roster `tier` field is a constrained enum (remote_provider_core.TIER_CHOICES), and
# NVIDIA deliberately stays `unknown` there: it publishes no per-account quota, so
# `renewing_free` would be a promise the provider does not make. But `unknown` UNDERSTATES
# what we have actually measured -- the operator established 2026-09-19 that there is no
# daily quota at all, and that the one real ceiling is ~40 requests per MINUTE. A bare
# "unknown" therefore hides a known fact. This function keeps the enum honest and the
# DISPLAY informative, rather than corrupting the enum to fix a label.
#
# Scoped to NVIDIA on purpose: the measurement is about NVIDIA only and must not be
# generalised to Gemini/Groq/others, which have their own (smaller) quotas.
_tier_label() {
    local tier="$1" prov="$2"
    if [[ "$prov" == nvidia && "$tier" == unknown ]]; then
        printf 'no daily cap/40/min'
    else
        printf '%s' "$tier"
    fi
}

# ---- local-capable filter helpers -------------------------------------------
# Build a bash associative array from _POLICY_JSON for fast lookup.
declare -A _LC_VISIBLE
_lc_load_policy() {
    _LC_VISIBLE=()
    if [[ -z "${_POLICY_JSON:-}" ]]; then return; fi
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        local v alias prov mid
        v="$(printf '%s' "$line" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print("true" if d.get("visible",True) else "false")')"
        alias="$(printf '%s' "$line" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print(d.get("alias",""))')"
        prov="$(printf '%s' "$line" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print(d.get("provider",""))')"
        mid="$(printf '%s' "$line" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());print(d.get("remote_model_id",""))')"
        _LC_VISIBLE["${prov}|${mid}"]="${v:-true}"
    done <<< "$(printf '%s' "$_POLICY_JSON" | python3 -c 'import json,sys;d=json.loads(sys.stdin.read());[print(json.dumps(r)) for r in d.get("rows",[])]' 2>/dev/null)"
}
_lc_is_hidden() {
    # $1=provider $2=model_id -> 0 if hidden, 1 if visible
    local key="${1}|${2}"
    local v="${_LC_VISIBLE[$key]:-true}"
    [[ "$v" == "false" ]]
}

# _filtered_aliases -> prints alias names, one per line, filtered by tier + local-capable.
_filtered_aliases() {
    local e alias prov model disp tier
    for e in "${LA_REMOTE_AGENTS[@]}"; do
        alias="$(_field "$e" 1)"; prov="$(_field "$e" 2)"; model="$(_field "$e" 3)"
        disp="$(_field "$e" 4)"; tier="$(_field "$e" 5)"
        _visible "$tier" || continue
        if [[ $LOCAL_CAPABLE_SHOWN -eq 0 ]]; then
            _lc_is_hidden "$prov" "$model" && continue
        fi
        echo "$alias"
    done
}

# _nav -> writes a navigation token for the csl parent to read back. csl runs this
# script as a child process (`bash "$rl" ...`), so `$$` here is the CHILD's pid, not
# csl's — a file keyed on this script's own $$ can never be found by the parent's
# read using ITS $$. csl passes the exact path via CSL_NAV_FILE; fall back to $$ only
# for a standalone --csl-owner invocation with no parent to hand a path to.
# Format: first line = navigation target (home/local/quit), subsequent lines = KEY=VALUE state sync
_nav() {
    local target="$1"
    shift
    local navfile="${CSL_NAV_FILE:-/tmp/_csl_nav.$$}"
    {
        echo "$target"
        # Sync state variables that the parent (csl) needs to know about
        [[ -n "${AUTO_MODE_STATE:-}" ]] && echo "AUTO_MODE_STATE=$AUTO_MODE_STATE"
        [[ -n "${LOCAL_CAPABLE_SHOWN:-}" ]] && echo "LOCAL_CAPABLE=$LOCAL_CAPABLE_SHOWN"
        [[ -n "${TELEMETRY_ENABLED:-}" ]] && echo "TELEMETRY=$TELEMETRY_ENABLED"
        [[ -n "${INCLUDE_TRIALS:-}" ]] && echo "INCLUDE_TRIALS=$INCLUDE_TRIALS"
        [[ -n "${EFFORT_CHOICE:-}" ]] && echo "EFFORT_CHOICE=$EFFORT_CHOICE"
    } > "$navfile"
}

# ---- local-capable filter ---------------------------------------------------
# Load the policy file if it exists, so the interactive picker can filter
# the roster. The filter is re-applied every render so toggle state is live.
# MUST run after _field/_lc_load_policy are defined above — this was originally
# a top-level call issued before either function existed (bash has no forward
# declarations), which made every remote-session.sh invocation print
# "_lc_load_policy: command not found" and silently skip the filter entirely.
POLICY_FILE="$REPO_ROOT/config/local-capable-remote-models.psv"
if [[ -r "$POLICY_FILE" ]]; then
  _POLICY_JSON="$(bash "$SCRIPT_DIR/local-capable-filter.sh" --parse "$POLICY_FILE" --roster - 2>/dev/null <<< "$(
    for e in "${LA_REMOTE_AGENTS[@]}"; do
      printf '%s\n' "$e"
    done | while IFS= read -r line; do
      alias="$(_field "$line" 1)"; prov="$(_field "$line" 2)"; model="$(_field "$line" 3)"
      disp="$(_field "$line" 4)"; tier="$(_field "$line" 5)"
      printf '%s|%s|%s|%s|%s\n' "$alias" "$prov" "$model" "$disp" "$tier"
    done
  )")" || _POLICY_JSON='{"visible_count":0,"hidden_count":0,"rows":[]}'
else
  _POLICY_JSON='{"visible_count":0,"hidden_count":0,"rows":[]}'
fi
_lc_load_policy

print_list() {
    printf '\n\033[1m%s  REMOTE cloud-API agents\033[0m  (provider quotas/billing apply; catalog listing is not a tool-use test)\n\n' "$SESSION_EMOJI_FREE_API"
    printf '  %-3s %-25s %-37s %-15s %s\n' '#' 'ALIAS' 'DISPLAY' 'TIER' 'KEY'
    local i=0 e alias prov model disp tier keystate
    local hidden_count=0
    for e in "${LA_REMOTE_AGENTS[@]}"; do
        alias="$(_field "$e" 1)"; prov="$(_field "$e" 2)"; model="$(_field "$e" 3)"
        disp="$(_field "$e" 4)"; tier="$(_field "$e" 5)"
        _visible "$tier" || continue
        # This is the same presentation-only filter the interactive menu applies (see
        # _run_remote_menu) — --list is a display surface too, so it must not show a
        # local-capable model by default just because it bypasses the interactive picker.
        if [[ $LOCAL_CAPABLE_SHOWN -eq 0 ]] && _lc_is_hidden "$prov" "$model"; then
            hidden_count=$((hidden_count+1))
            continue
        fi
        i=$((i+1))
        if "$KEYS" --check "$prov" >/dev/null 2>&1; then keystate="✓ $prov"; else keystate="✗ $prov (no key)"; fi
        printf '  %-3s %-25s %-37s %-15s %s\n' "$i" "$alias" "$disp" "$(_tier_label "$tier" "$prov")" "$keystate"
    done
    [[ $INCLUDE_TRIALS -eq 0 ]] && printf '\n  (trial-tier agents hidden — pass --include-trials to show them)\n'
    if [[ $hidden_count -gt 0 ]]; then
        printf '  (%d local-capable model(s) hidden — pass --local-capable-shown to show them)\n' "$hidden_count"
    fi
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

# ---- interactive remote picker menu loop ------------------------------------
# Replaces the one-shot pick_alias with a full menu that supports:
#   - switching back to the home lane / local picker
#   - toggling local-capable hidden/visible
#   - viewing the hidden-model report grouped by provider
# When --csl-owner is set, navigation returns via /tmp/_csl_nav.$$ instead
# of exiting. In that case the function returns 0 and the caller reads the
# nav token.
_run_remote_menu() {
    # ALIAS may be pre-set from CLI args; if so skip the picker.
    if [[ -n "$ALIAS" ]]; then return 0; fi

    local -a choices=() e alias tier
    local selection=""
    local -a current_choices=()

    # The whole interactive loop (menu box, prompts, reports) is redirected to stderr as
    # a group. This function's ONLY stdout output is the final `printf '%s' "$selection"`
    # after the loop — callers capture it via `ALIAS="$(_run_remote_menu)"`. Without this,
    # every echo/printf that draws the menu went to stdout too, so the entire visible menu
    # was silently swallowed into $ALIAS instead of shown to the user, and $ALIAS ended up
    # holding menu text (or, on quit/navigate, nothing at all) rather than a real selection.
    # `{ ... } >&2` is a redirected group, not a subshell, so variable assignments made
    # inside (selection=, AUTO_MODE_STATE=, etc.) still reach the rest of the function.
    {
    while [[ -z "$selection" ]]; do
        # Rebuild the visible roster each render.
        choices=()
        for e in "${LA_REMOTE_AGENTS[@]}"; do
            tier="$(_field "$e" 5)"; _visible "$tier" || continue
            if [[ $LOCAL_CAPABLE_SHOWN -eq 0 ]]; then
                alias="$(_field "$e" 1)"; prov="$(_field "$e" 2)"; model="$(_field "$e" 3)"
                _lc_is_hidden "$prov" "$model" && continue
            fi
            choices+=("$(_field "$e" 1)")
        done

        current_choices=("${choices[@]}")

        echo
        local hidden_count=0
        for e in "${LA_REMOTE_AGENTS[@]}"; do
            tier="$(_field "$e" 5)"; _visible "$tier" || continue
            if [[ $LOCAL_CAPABLE_SHOWN -eq 0 ]]; then
                alias="$(_field "$e" 1)"; prov="$(_field "$e" 2)"; model="$(_field "$e" 3)"
                _lc_is_hidden "$prov" "$model" && hidden_count=$((hidden_count+1))
            fi
        done
        _box top
        _box center "$SESSION_EMOJI_FREE_API Remote API Session Picker"
        _box mid
        _box row "$(printf '  %d model(s) visible  (hidden: %d)' \
                    "${#choices[@]}" "$hidden_count")"
        _box mid
        printf '  %-3s %-25s %-37s %-15s %s\n' '#' 'ALIAS' 'DISPLAY' 'TIER' 'KEY'
        local i=0 keystate
        for e in "${LA_REMOTE_AGENTS[@]}"; do
            alias="$(_field "$e" 1)"; prov="$(_field "$e" 2)"; model="$(_field "$e" 3)"
            disp="$(_field "$e" 4)"; tier="$(_field "$e" 5)"
            _visible "$tier" || continue
            if [[ $LOCAL_CAPABLE_SHOWN -eq 0 ]]; then
                _lc_is_hidden "$prov" "$model" && continue
            fi
            i=$((i+1))
            if "$KEYS" --check "$prov" >/dev/null 2>&1; then keystate="✓ $prov"; else keystate="✗ $prov (no key)"; fi
            printf '  %-3s %-25s %-37s %-15s %s\n' "$i" "$alias" "$disp" "$(_tier_label "$tier" "$prov")" "$keystate"
        done
        [[ $INCLUDE_TRIALS -eq 0 ]] && echo "  (trial-tier hidden — pass --include-trials to show)"
        echo
        echo "  h) $EMOJI_HOME back to lane selector"
        echo "  e) $EMOJI_EFFORT effort: ${EFFORT_CHOICE:-<provider default>}"
        echo "  s) $SESSION_EMOJI_LOCAL switch to local models"
        echo "  f) 🔍 locally-runnable models: $([ "$LOCAL_CAPABLE_SHOWN" = "1" ] && echo "SHOWN" || echo "HIDDEN")"
        echo "  R) 📋 show hidden-model report"
        echo "  k) $EMOJI_KEY set up remote API keys"
        case "$AUTO_MODE_STATE" in
            0) echo "  a) $EMOJI_AUTO_MODE auto-mode: blind-trust — auto with no classifier (cycle)" ;;
            1) echo "  a) $EMOJI_AUTO_MODE auto-mode: classifier  — auto with local classifier (cycle)" ;;
            2) echo "  a) $EMOJI_AUTO_MODE auto-mode: off         — acceptEdits; no classifier (cycle)" ;;
        esac
        if [ "$TELEMETRY_ENABLED" = "1" ]; then
            echo "  t) $EMOJI_TELEMETRY_ON telemetry: ON  — stock Claude Code reporting/update checks (toggle)"
        else
            echo "  t) $EMOJI_TELEMETRY_ON telemetry: OFF — no nonessential outbound traffic (toggle)"
        fi
        echo "  l) ⏳ limited trial providers: $([ "$INCLUDE_TRIALS" = "1" ] && echo "SHOWN" || echo "HIDDEN")"
        echo "  q) quit"
        echo
        printf "Select [1-%d] (h/e/s/f/R/k/a/t/l/q): " "${#choices[@]}" >&2
        read -r -p "" sel >&2 || { _nav "quit"; return 0; }
        case "$sel" in
            h|H) _nav "home"; return 0 ;;
            s|S) _nav "local"; return 0 ;;
            f|F)
                LOCAL_CAPABLE_SHOWN=$(( 1 - LOCAL_CAPABLE_SHOWN ))
                _lc_load_policy
                continue ;;
            R|r)
                # Show hidden-model report
                echo
                bash "$SCRIPT_DIR/local-capable-filter.sh" --report "$POLICY_FILE" --roster - 2>/dev/null <<< "$(
                    for e in "${LA_REMOTE_AGENTS[@]}"; do
                        printf '%s\n' "$e"
                    done | while IFS= read -r line; do
                        printf '%s\n' "$line"
                    done
                )" || echo "  (report unavailable)"
                echo
                echo "  (press enter to return to menu)" >&2
                read -r -p "" _ >&2 || { _nav "quit"; return 0; }
                continue ;;
            k|K) python3 "$REPO_ROOT/install/setup-api-keys.py"; continue ;;
            a|A) AUTO_MODE_STATE=$(( (AUTO_MODE_STATE + 1) % 3 )); continue ;;
            t|T)
                if [[ "$sel" == "t" ]]; then
                    TELEMETRY_ENABLED=$(( 1 - TELEMETRY_ENABLED ))
                else
                    INCLUDE_TRIALS=$(( 1 - INCLUDE_TRIALS ))
                fi
                continue ;;
            l|L)
                INCLUDE_TRIALS=$(( 1 - INCLUDE_TRIALS ))
                continue ;;
            e|E)
                # Select effort level (compatible with Claude's --effort flag)
                local efforts=("low" "medium" "high" "xhigh" "max")
                local def=2  # medium
                if [[ -n "$EFFORT_CHOICE" ]]; then
                    for idx in "${!efforts[@]}"; do
                        [[ "${efforts[$idx]}" == "$EFFORT_CHOICE" ]] && def=$((idx + 1)) && break
                    done
                fi
                local i=1
                echo "  Effort levels (higher = more thinking, slower):"
                for eff in "${efforts[@]}"; do
                    printf "    %d) %s\n" "$i" "$eff" >&2
                    i=$((i+1))
                done
                local c; printf "  Select effort [%d]: " "$def" >&2; read -r c >&2; c="${c:-$def}"
                if [[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -ge 1 ] && [ "$c" -le ${#efforts[@]} ]; then
                    EFFORT_CHOICE="${efforts[$((c-1))]}"
                    echo "  Effort set to: $EFFORT_CHOICE" >&2
                else
                    echo "  Invalid selection, keeping: ${EFFORT_CHOICE:-<provider default>}" >&2
                fi
                continue ;;
            q|Q) _nav "quit"; return 0 ;;
            *)
                if ! [[ "$sel" =~ ^[0-9]+$ ]] || [ "$sel" -lt 1 ] || [ "$sel" -gt "${#choices[@]}" ]; then
                    echo "  Invalid selection." >&2
                    continue
                fi
                selection="${current_choices[$((sel-1))]}"
                ;;
        esac
    done
    } >&2
    # Return the selected alias via stdout (compatible with existing callers).
    printf '%s' "$selection"
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
    printf '  provider   : %s\n  model      : %s\n  tier       : %s\n' "$prov" "$model" "$(_tier_label "$tier" "$prov")"
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
write_proxy_config() { # write_proxy_config <cfgpath> <provider> <model> <thinking> [effort]
    local cfg="$1" prov="$2" model="$3" thinking="$4" effort="${5:-}"
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

    # EFFORT. Claude Code's own --effort flag is meaningless to a third-party provider: it is
    # interpreted by Anthropic's models, so passing it to `claude` while the request is proxied
    # to NVIDIA/Gemini/Groq changed nothing. Effort has to travel IN THE REQUEST BODY, which is
    # what this proxy config controls. The field differs per model family, so map it:
    #   * reasoning_effort (low|medium|high) -- the OpenAI-compatible spelling. LiteLLM
    #     translates it per provider, so it is the right default for OpenAI-shaped routes.
    #   * NVIDIA Nemotron does NOT take reasoning_effort; it gates reasoning with
    #     enable_thinking inside chat_template_kwargs. So on Nemotron an explicit effort means
    #     "turn thinking ON" (the default above turns it off to avoid the thinking-block crash).
    # Claude Code offers five levels; the OpenAI field accepts three, so xhigh/max fold to high.
    local mapped_effort=""
    case "$effort" in
        low)              mapped_effort="low" ;;
        medium)           mapped_effort="medium" ;;
        high|xhigh|max)   mapped_effort="high" ;;
        "")               mapped_effort="" ;;
        *)                echo "remote-session: unknown effort '\''$effort'\'', ignoring" >&2 ;;
    esac
    local effort_line=""
    if [[ -n "$mapped_effort" ]]; then
        case "$prov" in
            nvidia)
                # Nemotron reads enable_thinking, not reasoning_effort. An explicit effort is a
                # request TO reason, so enable it and state the budget the family understands.
                if [[ "$model" == *nemotron* ]]; then
                    think_line='      chat_template_kwargs:
        enable_thinking: true'
                else
                    effort_line="      reasoning_effort: $mapped_effort"
                fi
                ;;
            gemini)
                # Gemini thinking is disabled above unless asked for; an explicit effort asks.
                [[ "$thinking" != "true" ]] && think_line=""
                effort_line="      reasoning_effort: $mapped_effort"
                ;;
            *)  effort_line="      reasoning_effort: $mapped_effort" ;;
        esac
    fi

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
            [[ -n "$effort_line" ]] && echo "$effort_line"
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

start_proxy() { # start_proxy <provider> <model> <thinking> [effort] -> echoes port
    local prov="$1" model="$2" thinking="$3" effort="${4:-}"
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
    write_proxy_config "$cfg" "$prov" "$model" "$thinking" "$effort" || return 1
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
          # shellcheck disable=SC2163 # line_o is a full NAME=VALUE string from remote-keys.sh --env; export "NAME=VALUE" is valid bash
          while IFS= read -r line_o; do export "$line_o"; done <<< "$envassign_o"
          exec "${_cmd[@]}" --config "$cfg" --port "$port" ) > "$log" 2>&1 &
        echo $! > "$pidf"
        local j
        # shellcheck disable=SC2034  # retry counter, not read
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
      # shellcheck disable=SC2163 # line is a full NAME=VALUE string from remote-keys.sh --env; export "NAME=VALUE" is valid bash
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
CLAUDE_EXTRA_ARGS=()
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
        --local-capable-shown) LOCAL_CAPABLE_SHOWN=1; shift ;;
        --csl-owner)
            # shellcheck disable=SC2034  # accepted for forward-compat / documentation of the --csl-owner contract; not currently read (see report)
            CSL_OWNER=1; shift ;;
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
    # _run_remote_menu returns 0 (not an error) on navigation (h/l/q) — it wrote a
    # nav token via _nav() and printed nothing on stdout. `ALIAS="$(...)" || ...` only
    # catches a NON-ZERO exit, so an empty-but-successful return used to fall straight
    # through into alias resolution below with ALIAS="", producing a bogus
    # "unknown remote alias: " error instead of a clean exit. Check emptiness explicitly.
    ALIAS="$(_run_remote_menu)" || { echo "remote-session: nothing selected." >&2; exit 1; }
    if [[ -z "$ALIAS" ]]; then
        # Navigated away (home/local) or quit — csl reads the nav token itself when
        # CSL_OWNER=1; a direct invocation with nothing selected just exits cleanly.
        exit 0
    fi
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

    # Blind-trust auto mode (AUTO_MODE_STATE=0) needs sandbox.enabled=true to bypass
    # the cloud classifier for too-complex commands. Create/use a settings file.
    if [[ "$AUTO_MODE_STATE" -eq 0 ]]; then
        # Support user-provided settings file via LA_REMOTE_CLAUDE_SETTINGS
        if [[ -n "${LA_REMOTE_CLAUDE_SETTINGS:-}" ]]; then
            if [[ -f "$LA_REMOTE_CLAUDE_SETTINGS" ]]; then
                BLIND_TRUST_SETTINGS_FILE="$LA_REMOTE_CLAUDE_SETTINGS"
                BLIND_TRUST_SETTINGS_USER_PROVIDED=1
            else
                echo "⚠️  LA_REMOTE_CLAUDE_SETTINGS file not found: $LA_REMOTE_CLAUDE_SETTINGS; falling back to generated settings" >&2
                # Create default blind-trust settings with sandbox.enabled=true
                BLIND_TRUST_SETTINGS_FILE="${TMPDIR:-/tmp}/claude-blind-trust-settings.json"
                cat > "$BLIND_TRUST_SETTINGS_FILE" <<'SETTINGS_EOF'
{
  "_comment": "Blind-trust settings for a remote free-API session. sandbox.enabled changes the WRITE BOUNDARY; it does NOT stop the classifier being consulted, so the verbs a session needs must be allowlisted explicitly or every one of them prompts. Measured 2026-09-20: a bare mkdir prompted until it was listed here.",
  "permissions": {
    "defaultMode": "acceptEdits",
    "allow": [
      "Bash(ls:*)",
      "Bash(cat:*)",
      "Bash(echo:*)",
      "Bash(mkdir:*)",
      "Bash(touch:*)",
      "Bash(printf:*)",
      "Bash(cp:*)",
      "Bash(mv:*)",
      "Bash(grep:*)",
      "Bash(find:*)",
      "Bash(cd:*)",
      "Bash(pwd:*)",
      "Bash(wc:*)",
      "Bash(head:*)",
      "Bash(tail:*)",
      "Bash(sed:*)",
      "Bash(awk:*)",
      "Bash(cut:*)",
      "Bash(sort:*)",
      "Bash(uniq:*)",
      "Bash(tr:*)",
      "Bash(diff:*)",
      "Bash(cmp:*)",
      "Bash(od:*)",
      "Bash(file:*)",
      "Bash(stat:*)",
      "Bash(readlink:*)",
      "Bash(basename:*)",
      "Bash(dirname:*)",
      "Bash(realpath:*)",
      "Bash(which:*)",
      "Bash(command:*)",
      "Bash(true:*)",
      "Bash(test:*)",
      "Bash(date:*)",
      "Bash(env:*)",
      "Bash(shasum:*)",
      "Bash(md5:*)",
      "Bash(jq:*)",
      "Bash(python3:*)",
      "Bash(bash:*)",
      "Bash(sh:*)",
      "Bash(zsh:*)",
      "Bash(curl:*)",
      "Bash(lsof:*)",
      "Bash(ps:*)",
      "Bash(df:*)",
      "Bash(du:*)",
      "Bash(uname:*)",
      "Bash(sysctl:*)",
      "Bash(vm_stat:*)",
      "Bash(xattr:*)",
      "Bash(mktemp:*)",
      "Bash(tee:*)",
      "Bash(xargs:*)",
      "Bash(pytest:*)",
      "Bash(npm:*)",
      "Bash(node:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)",
      "Bash(git show:*)",
      "Bash(git branch:*)",
      "Bash(git rev-parse:*)",
      "Bash(git rev-list:*)",
      "Bash(git for-each-ref:*)",
      "Bash(git ls-files:*)",
      "Bash(git tag:*)",
      "Bash(git add:*)",
      "Bash(git commit:*)",
      "Bash(git checkout:*)",
      "Bash(git merge:*)",
      "Bash(git stash:*)",
      "Bash(git worktree:*)",
      "Bash(gh release view:*)",
      "Bash(gh release list:*)",
      "Bash(gh release create:*)",
      "Bash(gh api:*)",
      "Bash(waypoints.py:*)",
      "Bash(interrupted:*)",
      "Bash(claude plugin update:*)",
      "Bash(csl:*)",
      "Bash(la-roles.sh:*)",
      "Bash(la-session-identity.sh:*)",
      "Bash(local-llm-hotswap.sh:*)",
      "Bash(librarian-dispatch.py:*)",
      "Bash(remote-provider-doctor.py:*)"
    ]
  },
  "sandbox": {
    "enabled": true
  }
}
SETTINGS_EOF
            fi
        else
            # Create default blind-trust settings with sandbox.enabled=true
            BLIND_TRUST_SETTINGS_FILE="${TMPDIR:-/tmp}/claude-blind-trust-settings.json"
            cat > "$BLIND_TRUST_SETTINGS_FILE" <<'SETTINGS_EOF'
{
  "_comment": "Blind-trust settings for a remote free-API session. sandbox.enabled changes the WRITE BOUNDARY; it does NOT stop the classifier being consulted, so the verbs a session needs must be allowlisted explicitly or every one of them prompts. Measured 2026-09-20: a bare mkdir prompted until it was listed here.",
  "permissions": {
    "defaultMode": "acceptEdits",
    "allow": [
      "Bash(ls:*)",
      "Bash(cat:*)",
      "Bash(echo:*)",
      "Bash(mkdir:*)",
      "Bash(touch:*)",
      "Bash(printf:*)",
      "Bash(cp:*)",
      "Bash(mv:*)",
      "Bash(grep:*)",
      "Bash(find:*)",
      "Bash(cd:*)",
      "Bash(pwd:*)",
      "Bash(wc:*)",
      "Bash(head:*)",
      "Bash(tail:*)",
      "Bash(sed:*)",
      "Bash(awk:*)",
      "Bash(cut:*)",
      "Bash(sort:*)",
      "Bash(uniq:*)",
      "Bash(tr:*)",
      "Bash(diff:*)",
      "Bash(cmp:*)",
      "Bash(od:*)",
      "Bash(file:*)",
      "Bash(stat:*)",
      "Bash(readlink:*)",
      "Bash(basename:*)",
      "Bash(dirname:*)",
      "Bash(realpath:*)",
      "Bash(which:*)",
      "Bash(command:*)",
      "Bash(true:*)",
      "Bash(test:*)",
      "Bash(date:*)",
      "Bash(env:*)",
      "Bash(shasum:*)",
      "Bash(md5:*)",
      "Bash(jq:*)",
      "Bash(python3:*)",
      "Bash(bash:*)",
      "Bash(sh:*)",
      "Bash(zsh:*)",
      "Bash(curl:*)",
      "Bash(lsof:*)",
      "Bash(ps:*)",
      "Bash(df:*)",
      "Bash(du:*)",
      "Bash(uname:*)",
      "Bash(sysctl:*)",
      "Bash(vm_stat:*)",
      "Bash(xattr:*)",
      "Bash(mktemp:*)",
      "Bash(tee:*)",
      "Bash(xargs:*)",
      "Bash(pytest:*)",
      "Bash(npm:*)",
      "Bash(node:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)",
      "Bash(git show:*)",
      "Bash(git branch:*)",
      "Bash(git rev-parse:*)",
      "Bash(git rev-list:*)",
      "Bash(git for-each-ref:*)",
      "Bash(git ls-files:*)",
      "Bash(git tag:*)",
      "Bash(git add:*)",
      "Bash(git commit:*)",
      "Bash(git checkout:*)",
      "Bash(git merge:*)",
      "Bash(git stash:*)",
      "Bash(git worktree:*)",
      "Bash(gh release view:*)",
      "Bash(gh release list:*)",
      "Bash(gh release create:*)",
      "Bash(gh api:*)",
      "Bash(waypoints.py:*)",
      "Bash(interrupted:*)",
      "Bash(claude plugin update:*)",
      "Bash(csl:*)",
      "Bash(la-roles.sh:*)",
      "Bash(la-session-identity.sh:*)",
      "Bash(local-llm-hotswap.sh:*)",
      "Bash(librarian-dispatch.py:*)",
      "Bash(remote-provider-doctor.py:*)"
    ]
  },
  "sandbox": {
    "enabled": true
  }
}
SETTINGS_EOF
        fi
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
    # Map effort levels to Claude's choices (must match --effort flag values)
    efforts=("low" "medium" "high" "xhigh" "max")
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
    # NVIDIA is not "unverified" -- it is MEASURED (operator, 2026-09-19): no known daily
    # quota, but ~40 requests per minute is the ceiling, so bursts are what trip it rather
    # than cumulative use. Dated on purpose: this is an observation, not a provider promise,
    # and it says nothing about token cost (never claim $0/token for a free tier).
    *) if [[ "$PROV" == nvidia ]]; then
           COST_NOTE='no known daily quota (measured 2026-09-19); ceiling is 40 requests per minute -- pace bursts'
       else
           COST_NOTE='account quota and billing unverified; do not assume free'
       fi ;;
esac
if [[ $DRY_RUN -eq 1 ]]; then
    echo "   DRY RUN  : would start a LiteLLM proxy and exec claude against it."
    echo "              nothing started, no network call made."
    echo "     model          : $MODEL"
    echo "     provider       : $PROV"
    echo "     cost note      : $COST_NOTE"
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
    if [[ "$AUTO_MODE_STATE" -eq 0 && -n "$BLIND_TRUST_SETTINGS_FILE" ]]; then
        if [[ "$BLIND_TRUST_SETTINGS_USER_PROVIDED" -eq 1 ]]; then
            echo "     settings       : --settings $BLIND_TRUST_SETTINGS_FILE (user-provided via LA_REMOTE_CLAUDE_SETTINGS)"
        else
            echo "     settings       : --settings $BLIND_TRUST_SETTINGS_FILE (generated blind-trust with sandbox.enabled=true)"
        fi
    fi
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
PORT="$(start_proxy "$PROV" "$MODEL" "$THINKING" "$EFFORT_CHOICE")" || {
    echo "remote-session: could not start the translating proxy." >&2; exit 1; }
echo "   proxy    : ready on http://127.0.0.1:$PORT"
echo



export ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT"   # NO /v1 — Claude Code appends /v1/messages
export ANTHROPIC_AUTH_TOKEN="sk-local-agents-remote"
export ANTHROPIC_API_KEY="sk-local-agents-remote"
export CLAUDE_CODE_MAX_OUTPUT_TOKENS="$MAX_OUT"

# Streaming timeouts. The LOCAL launcher has carried these three since 2026-08-19; this lane
# shipped without them and so silently kept Claude Code's cloud-tuned ceilings. That is wrong
# for a free API in two ways: a big reasoning model (Nemotron 3 Ultra 550B) can spend well over
# a minute on first-token latency, and a free tier queues requests behind paying traffic. Either
# way the stream emits NOTHING while it waits, which the watchdog cannot distinguish from a hung
# connection -- it aborts and retries, surfacing as:
#   "Streaming response ended before any complete data was received. Retrying without streaming."
# It reproduces right after the FIRST prompt of a session because that turn carries the largest
# uncached prefill (full system prompt + tool definitions) and no warm cache to answer from.
#   API_TIMEOUT_MS           overall per-request cap.
#   API_FORCE_IDLE_TIMEOUT=0 disables the "no bytes arrived yet" abort on a slow first token.
#   CLAUDE_ENABLE_STREAM_WATCHDOG=0  the separate CLI 2.1.196 idle watchdog, on by default for
#     ALL providers, which the other two DO NOT cover. This is the one that actually bites.
# Bounded, not unbounded: API_TIMEOUT_MS still caps the request, so a genuinely dead stream ends.
export API_TIMEOUT_MS="${LA_REMOTE_API_TIMEOUT_MS:-600000}"
export API_FORCE_IDLE_TIMEOUT=0
export CLAUDE_ENABLE_STREAM_WATCHDOG=0
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1    # no telemetry through a third party
export CLAUDE_IS_REMOTE_API="true"                   # distinct from CLAUDE_IS_LOCAL
export LA_SESSION_LAUNCHER="remote-session.sh"       # names the launcher for plugin hooks (stop-hook gate)
export LA_QUEUE_STOP_HOOK="${LA_QUEUE_STOP_HOOK:-1}" # queued-prompt Stop hook; ON unless turned off
export LA_REMOTE_AGENT="$ALIAS"
export LA_REMOTE_PROVIDER="$PROV"

# Provide resolver inputs so it can emit truthful identity for remote sessions
export MODEL_ALIAS="$ALIAS"                          # the remote agent alias (e.g. nvidia-nemotron-3-ultra)
export LA_CUR_EFFORT="${EFFORT_CHOICE:-medium}"      # effort level from launcher
export LA_CUR_THINK="$THINKING"                      # thinking mode (true/false)
export LA_CUR_ROLES="operator"                       # default role for remote sessions
export LA_CUR_REPO=""                                # no HF repo for remote models
export LA_CUR_SIZE=""                                # unknown size
export LA_CUR_SERVE="litellm"                        # backend is LiteLLM proxy
export LA_CUR_TOOLP=""                               # no tool parser for remote
export LA_CUR_REASONP=""                             # no reasoning parser for remote
export LA_REMOTE_MODEL="$MODEL"                      # actual provider model ID (e.g. nvidia_nim/nemotron-3-ultra)

# SESSION ID GENERATION — create stable session ID before identity resolution.
# This ID persists across the transcript lifecycle and enables transition detection.
# Format: YYYYMMDD-HHMMSS-PID-alias-hash
_ts=$(date -u +"%Y%m%d-%H%M%S")
_pid=$$
_alias_hash=$(printf '%s' "${ALIAS:-unknown}" | cksum | cut -d' ' -f1 | cut -c1-6)
LA_SESSION_ID="${_ts}-${_pid}-${_alias_hash}"
export LA_SESSION_ID

# SESSION IDENTITY RESOLUTION — emit deterministic identity for consumers
# (statusline, transcript marker, hooks). Must run AFTER endpoint is known.
if [ -x "$SCRIPT_DIR/la-session-identity.sh" ]; then
    SESSION_IDENTITY=$("$SCRIPT_DIR/la-session-identity.sh" 2>/dev/null || true)
    if [ -n "$SESSION_IDENTITY" ]; then
        export LA_SESSION_IDENTITY="$SESSION_IDENTITY"
        # Export individual fields for easy consumption by hooks/statusline
        export LA_SESSION_KIND=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"session_kind":"[^"]*"' | cut -d'"' -f4)
        export LA_ACTUAL_MODEL=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"actual_model_id":"[^"]*"' | cut -d'"' -f4)
        export LA_PROVIDER_DISPLAY=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"provider_display":"[^"]*"' | cut -d'"' -f4)
        export LA_THEME_IDENTIFIER=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"theme_identifier":"[^"]*"' | cut -d'"' -f4)
        export LA_SPINNER_PROFILE=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"spinner_profile_id":"[^"]*"' | cut -d'"' -f4)
        export LA_TRANSCRIPT_MARKER_VERSION=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"transcript_marker_version":[0-9]*' | cut -d':' -f2)
        export LA_SESSION_KIND_EMOJI=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"session_emoji":"[^"]*"' | cut -d'"' -f4)
        export LA_SESSION_ID=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"session_id":"[^"]*"' | cut -d'"' -f4)
    fi
fi

# PER-SESSION SETTINGS — generate theme + spinner overlay for remote sessions only.
# This creates a transient settings file passed via --settings, NOT written to user's
# persistent ~/.claude/settings.json. Only applied for free_api sessions (session_kind=free_api).
if [ "${LA_SESSION_KIND:-}" = "free_api" ] && [ -x "$SCRIPT_DIR/generate-remote-settings.py" ]; then
    SETTINGS_FILE="$(
        mktemp "${TMPDIR:-/tmp}/free-agents-settings.XXXXXX.json"
    )"
    chmod 600 "$SETTINGS_FILE"
    LAUNCH_DIR="$SCRIPT_DIR" "$SCRIPT_DIR/generate-remote-settings.py" \
        --identity-json "$LA_SESSION_IDENTITY" \
        --output "$SETTINGS_FILE" 2>/dev/null || true
    # Don't add to CLAUDE_EXTRA_ARGS yet — we may need to merge with blind-trust settings

    # THE THEME FILE must exist before the `theme` setting above can resolve to anything.
    # Custom themes are FILES: the CLI reads ~/.claude/themes/<slug>.json (verified against
    # 2.1.278; `claude --help` lists "custom themes" among what --safe-mode disables). Unlike
    # the settings overlay this is a PERSISTENT, user-visible file, so it is installed openly
    # rather than hidden in a temp dir -- a user opening ~/.claude/themes sees exactly what we
    # added, and a `[theme] watcher` picks up changes without a restart.
    #
    # Written only when ABSENT or CHANGED, so a user who hand-edits the colour keeps their
    # edit until our content genuinely differs, and repeated launches are a no-op.
    LA_THEME_DIR="$HOME/.claude/themes"
    LA_THEME_FILE="$LA_THEME_DIR/free-lime.json"
    if mkdir -p "$LA_THEME_DIR" 2>/dev/null; then
        LA_THEME_NEW="$(LAUNCH_DIR="$SCRIPT_DIR" "$SCRIPT_DIR/generate-remote-settings.py" \
            --emit-theme-file 2>/dev/null || true)"
        if [ -n "$LA_THEME_NEW" ]; then
            if [ ! -f "$LA_THEME_FILE" ] || [ "$LA_THEME_NEW" != "$(cat "$LA_THEME_FILE" 2>/dev/null)" ]; then
                printf '%s' "$LA_THEME_NEW" > "$LA_THEME_FILE" || true
            fi
        fi
    fi
fi

# STARTUP BANNER — after identity resolution so we have the theme emoji
# Add blind-trust sandbox info to banner if applicable
BLIND_TRUST_BANNER=""
if [[ "$AUTO_MODE_STATE" -eq 0 && -n "$BLIND_TRUST_SETTINGS_FILE" ]]; then
    BLIND_TRUST_BANNER="   sandbox  : enabled (bypasses cloud classifier for too-complex commands)"
fi

cat <<BANNER

╭──────────────────────────────────────────────────────────────╮
│  ${LA_SESSION_KIND_EMOJI:-$SESSION_EMOJI_FREE_API}  REMOTE API SESSION — $MODEL ($DISP)                    │
╰──────────────────────────────────────────────────────────────╯
   provider : $PROV      tier: $(_tier_label "$TIER" "$PROV")
   agent    : $ALIAS
   thinking : $THINKING
   cost     : $COST_NOTE
   privacy  : prompts and file contents LEAVE this machine → $PROV
$BLIND_TRUST_BANNER
BANNER

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

# SHARED shipping/verification rules, from the SAME file the local launcher appends. They are
# lane-independent — about how to verify and ship, not about where inference runs — so one file
# serves both and neither copy can drift. Inlined rather than pointed at, unlike the briefing
# index below: the index is reference material that grows, whereas these are the specific steps
# free sessions were measured SKIPPING, and a pointer to them gets ignored by exactly the models
# that need them. Hard-fail: a session missing them is indistinguishable from one that has them
# right up until it ships something broken.
: "${LA_SHARED_RULES_FILE:=$SCRIPT_DIR/../config/shared-agent-shipping-rules.txt}"
if [ -r "$LA_SHARED_RULES_FILE" ]; then
    AGENT_PROMPT="$AGENT_PROMPT

$(cat "$LA_SHARED_RULES_FILE")"
else
    printf 'ERROR: shared agent rules file is not readable: %s\n' "$LA_SHARED_RULES_FILE" >&2
    exit 1
fi

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

# Handle settings merge: if both blind-trust and per-session settings exist, merge them.
# Claude Code only accepts ONE --settings file (last wins), so we must merge.
FINAL_SETTINGS_FILE=""
if [[ "$AUTO_MODE_STATE" -eq 0 && -n "$BLIND_TRUST_SETTINGS_FILE" && -f "$BLIND_TRUST_SETTINGS_FILE" ]] && \
   [ "${LA_SESSION_KIND:-}" = "free_api" ] && [ -n "${SETTINGS_FILE:-}" ] && [ -f "$SETTINGS_FILE" ] && [ -s "$SETTINGS_FILE" ]; then
    # Both exist — merge them
    FINAL_SETTINGS_FILE="$(
        mktemp "${TMPDIR:-/tmp}/free-agents-merged-settings.XXXXXX.json"
    )"
    chmod 600 "$FINAL_SETTINGS_FILE"
    LAUNCH_DIR="$SCRIPT_DIR" "$SCRIPT_DIR/merge-settings.py" \
        --base "$BLIND_TRUST_SETTINGS_FILE" \
        --overlay "$SETTINGS_FILE" \
        --output "$FINAL_SETTINGS_FILE" 2>/dev/null || true
    if [ -f "$FINAL_SETTINGS_FILE" ] && [ -s "$FINAL_SETTINGS_FILE" ]; then
        claude_cmd+=(--settings "$FINAL_SETTINGS_FILE")
    else
        # Fallback: use blind-trust only
        claude_cmd+=(--settings "$BLIND_TRUST_SETTINGS_FILE")
    fi
elif [[ "$AUTO_MODE_STATE" -eq 0 && -n "$BLIND_TRUST_SETTINGS_FILE" && -f "$BLIND_TRUST_SETTINGS_FILE" ]]; then
    # Only blind-trust settings
    claude_cmd+=(--settings "$BLIND_TRUST_SETTINGS_FILE")
elif [ "${LA_SESSION_KIND:-}" = "free_api" ] && [ -n "${SETTINGS_FILE:-}" ] && [ -f "$SETTINGS_FILE" ] && [ -s "$SETTINGS_FILE" ]; then
    # Only per-session settings
    claude_cmd+=(--settings "$SETTINGS_FILE")
fi

# NOTE: --effort is Anthropic-side only; it does NOT reach a third-party provider. The
# mechanism that actually changes remote reasoning is the proxy config written by
# write_proxy_config (reasoning_effort / enable_thinking, per model family). This flag is
# kept so the CLI's own displayed state matches what the user picked.
if [[ -n "$EFFORT_CHOICE" ]]; then
    claude_cmd+=(--effort "$EFFORT_CHOICE")
fi
if [[ ${#PASSTHRU[@]} -gt 0 ]]; then
    claude_cmd+=("${PASSTHRU[@]}")
fi

# SESSION NAME — use provider/model + emoji for terminal title and /resume picker.
# Requires CLI 2.1.270+ (verified). Set via -n/--name flag.
SESSION_NAME="${LA_SESSION_KIND_EMOJI:-$SESSION_EMOJI_FREE_API} ${MODEL}"

# Add session name flag (same as local launcher) - insert after 'claude' (index 0)
claude_cmd=( "${claude_cmd[0]}" -n "$SESSION_NAME" "${claude_cmd[@]:1}" )

# For free_api sessions, wrap claude with telemetry wrapper to capture streaming token rate
if [[ "${LA_SESSION_KIND:-}" == "free_api" && -x "$SCRIPT_DIR/la-remote-telemetry-wrapper.sh" ]]; then
    # The wrapper expects: session_id followed by claude args
    wrapped_cmd=("$SCRIPT_DIR/la-remote-telemetry-wrapper.sh" "$LA_SESSION_ID" "${claude_cmd[@]}")
    ( _clear_provider_env
    "${wrapped_cmd[@]}" )
else
    ( _clear_provider_env
    "${claude_cmd[@]}" )
fi
