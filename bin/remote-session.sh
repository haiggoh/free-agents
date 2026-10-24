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
#   remote-session.sh                      # session picker, Remote screen (Quit, no Back)
#   remote-session.sh <alias>              # launch that remote agent
#   remote-session.sh --list               # print the remote roster and key status
#   remote-session.sh --verify <alias>     # check credential + model id live, launch nothing
#   remote-session.sh --dry-run <alias>    # print the plan and the env, start nothing
#   remote-session.sh --stop               # stop ORPHANED proxies (their session has exited)
#   remote-session.sh --stop <port> [--force]  # one proxy; --force even if its session is alive
#   remote-session.sh --swap-model <port> <alias> [--force]
#                                         # rescue a STALLED session: relaunch its proxy on
#                                         # another model, same port (pre-flight on a scratch
#                                         # port; backs up and reverts on any failure; refuses
#                                         # a session that answered recently unless --force)
#   remote-session.sh --include-trials     # also offer trial-tier (non-free) agents
#   remote-session.sh --models <alias>     # list catalog IDs, pricing and tool metadata (GET only)
#   remote-session.sh <alias> --remote-model <id> # explicitly choose the upstream model
#   remote-session.sh <alias> --effort LEVEL  # request low|medium|high|xhigh|max (omit = provider default)
#   remote-session.sh --inventory          # machine-readable roster for the picker (TSV)
#
# Environment:
#   LA_API_KEYS_DIR             credential dir (default ~/.api_keys)
#   LA_REMOTE_PROXY_PORT_MIN/MAX  proxy port scan range (default 4141-4151)
#   LA_REMOTE_MAX_OUTPUT_TOKENS   CLAUDE_CODE_MAX_OUTPUT_TOKENS (default 128000 = Claude Code's own
#                                 ceiling for the spoofed model; the proxy clamps per provider)
#   LA_REMOTE_KEEP_PROXY=1        leave the proxy running after the session exits
#   LA_REMOTE_ENABLE_MCP=1        enable MCPs for free-API sessions (default: 0, disabled)
#   LA_REMOTE_CLAUDE_SETTINGS     path to per-session settings JSON (merged with blind-trust settings
#                                 if both active; if set but missing, prints warning and continues)
#   LA_RTK_IN_FREE_API=1          keep rtk's Bash-output rewriting ON (default: off on this lane)
#                                 ignored in blind-trust auto-mode (AUTO_MODE_STATE=0)
#   LA_CLASSIFIER_MODEL=prov:model  auto-mode classifier (-a once) on a DEDICATED free model, e.g.
#                                 nvidia:nvidia/nemotron-3-super-120b-a12b (recommended; measured 3s/8s)
#                                 unset = the session model answers claude-sonnet-5 (works, slower)
#   LA_SUBAGENT_ROUTING=same|hybrid, LA_SUBAGENT_POOL=prov:model,…  subagent routing (see README)
#
# Cost: provider quota and billing apply; see the selected tier. This session
# does not use the Anthropic gateway. Catalog checks consume no generation tokens.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# A bare interactive launch opens the picker: draw its loading frame NOW, before ~0.7 s of
# roster/policy setup, so Terminal's "Last login … ; exit;" lines are covered at once.
# session-picker draws the same frame again (harmless) and Textual takes over the screen.
if [[ $# -eq 0 && -t 1 ]]; then
    _hg="$(sed -n 's/^EMOJI_LOADING="\([^"]*\)".*/\1/p' "$REPO_ROOT/config/emoji.sh" 2>/dev/null)"
    printf '\033[?1049h\033[2J\033[H\033[?25l  %s loading.  ' "${_hg:-…}"
    trap 'printf "\033[?25h\033[?1049l"' EXIT   # cleared below if we hand over to the picker
fi
ROSTER="$REPO_ROOT/config/remote-agents.sh"
KEYS="$SCRIPT_DIR/remote-keys.sh"
# Load the provider registry — single source of truth for endpoints,
# routing prefixes, and availability checks. Sourced, not executed.
source "$SCRIPT_DIR/remote-provider-registry.sh"
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
# la_mktemp_json STEM -> a unique, mode-600 temp file ending in .json. BSD mktemp only
# randomises TRAILING Xs, so "name.XXXXXX.json" was one literal file: the first launch
# worked, every later one hit "File exists" and silently lost its settings overlay
# (the theme). Inlined, not sourced: a missing helper must never break a launch.
la_mktemp_json() {
    local stem="$1" tmp
    tmp="$(mktemp "${stem}.XXXXXX")" || return 1
    if mv "$tmp" "$tmp.json" 2>/dev/null; then
        chmod 600 "$tmp.json" 2>/dev/null
        printf '%s\n' "$tmp.json"
    else
        rm -f "$tmp"; return 1
    fi
}

# Claude Code VERSION PROBE + the spoofed identity it can carry (0.32.0).
# Nemotron 3 Ultra has 1M actual context, but Claude Code enforces the SPOOFED model's
# limit. claude-opus-5 has 200k hardcoded; claude-opus-5-5 has 1M support and is the
# current generation (its cheaper rate card is what the phantom cost model should use).
# Minimum version for Opus 5.5: 2.1.280 (see Anthropic changelog).
# The version is probed ONCE and cached: the warning, --print-spoof and the launch flag
# must not drift apart by probing at different moments (derive, don't duplicate).
_CC_VERSION=""
_cc_probe() {
    [ -n "$_CC_VERSION" ] || _CC_VERSION="$(claude --version 2>/dev/null | awk '{print $1}')"
}
_cc_at_least() {  # _cc_at_least MIN -> exit 0 when the probed version >= MIN
    _cc_probe
    [ -n "$_CC_VERSION" ] || return 1   # unprobed: support is never CONFIRMED
    local min_version="$1"
    local IFS=.
    local c_major c_minor c_patch m_major m_minor m_patch
    read -r c_major c_minor c_patch <<< "$_CC_VERSION"
    read -r m_major m_minor m_patch <<< "$min_version"
    # Real version strings carry a 4th segment (this machine reports "2.1.296.b9d");
    # arithmetic on "296.b9d" is a syntax error that silently misclassifies a capable
    # CC as too old, so compare only the numeric prefix of the patch segment.
    c_patch="${c_patch%%[!0-9]*}"
    : "${c_major:=0}" "${c_minor:=0}" "${c_patch:=0}"
    if (( c_major < m_major )) || (( c_major == m_major && c_minor < m_minor )) || (( c_major == m_major && c_minor == m_minor && c_patch < m_patch )); then
        return 1
    fi
}
# The PRIMARY spoofed identity (0.32.0): claude-opus-5-5 when Claude Code supports it,
# with claude-opus-5 kept ONLY as the compatibility fallback (old or unprobed CC). Before
# 0.32.0 the launch flag was hardcoded to claude-opus-5, so the roster's claude-opus-5-5
# entry (added for 1M context) was never actually requested: sessions carried Opus 5's
# rate card and Claude Code's 200k ceiling. An unknown version falls back rather than
# forward on purpose: requesting an id an old Claude Code cannot carry would break
# sessions that worked.
_select_spoof_primary() {
    if _cc_at_least "2.1.280"; then
        printf 'claude-opus-5-5'
    else
        printf 'claude-opus-5'
    fi
}
_check_claude_version() {
    _cc_probe
    if ! _cc_at_least "2.1.280"; then
        [ -n "$_CC_VERSION" ] || return 0   # nothing probed -> nothing to warn about
        # stderr: this is a diagnostic, and --print-spoof's stdout is a contract.
        echo >&2
        echo "$EMOJI_WARNING  Your Claude Code version ($_CC_VERSION) is older than 2.1.280." >&2
        echo "    Opus 5.5 (claude-opus-5-5) with 1M context support requires 2.1.280 or newer." >&2
        echo "    The session will run as the compatibility spoof claude-opus-5 (200k limit)." >&2
        echo "    Update with: brew upgrade claude-code  (or your package manager)" >&2
        echo "    To suppress: export LA_SILENCE_CLAUDE_VERSION_WARNING=1" >&2
        echo >&2
    fi
}
# --help must not run anything (it used to call `claude --version` first): skip the probe for it.
case " $* " in *" -h "*|*" --help "*) ;; *) _check_claude_version ;; esac

# OUTPUT CEILING. Claude Code puts CLAUDE_CODE_MAX_OUTPUT_TOKENS into EVERY request as max_tokens,
# and LiteLLM lets the request's value override anything in the proxy config ({**litellm_params,
# **kwargs}), so THIS is the only knob that reaches the provider. The old 8192 default made long
# turns die with "Claude's response exceeded the 8192 output token maximum" whatever effort said.
# 128000 is the upper limit Claude Code itself accepts for the spoofed claude-opus-5 (it clamps
# anything higher, measured on 2.1.291). Providers with a LOWER cap (Groq: 65536 gpt-oss, 16384
# qwen) answer 400 with their number; la_proxy_hooks.py learns it and retries clamped.
MAX_OUT="${LA_REMOTE_MAX_OUTPUT_TOKENS:-128000}"
if ! [[ "$MAX_OUT" =~ ^[1-9][0-9]*$ ]]; then
    echo "remote-session: LA_REMOTE_MAX_OUTPUT_TOKENS must be a positive integer, got '$MAX_OUT'; using 128000" >&2
    MAX_OUT=128000
fi
INCLUDE_TRIALS=0
: "${DRY_RUN:=0}"
MODE="launch"
ALIAS=""
REMOTE_MODEL=""
# Local-capable filter: 0=hidden (default), 1=shown.
LOCAL_CAPABLE_SHOWN=0
# Show broken models: 0=hidden (default), 1=shown.
SHOW_BROKEN_MODELS=0
# Show models over the age cutoff (LA_REMOTE_MAX_MODEL_AGE_MONTHS, default 18): 0=hidden (default).
# Only the picker's catalog rows carry dates, so this seeds the picker's initial state.
SHOW_OLD_MODELS=0
# When true, remote-session.sh runs as a submenu of csl and returns
# via a navigation token on stdout instead of exec'ing claude.
CSL_OWNER=0
# Blind-trust settings file (set when AUTO_MODE_STATE=0)
BLIND_TRUST_SETTINGS_FILE=""
TEMPERATURE_CHOICE=""
# Show paid-only tier models: 0=hidden (default), 1=shown.
SHOW_PAID_ONLY_MODELS=0

usage() {
    sed -n '2,/^set -uo pipefail/{ /^set -uo pipefail/d; s/^# \{0,1\}//; p; }' "$0"
    echo ""
    echo "Additional options:"
    echo "  -i, --install-keys    $EMOJI_KEY  Install / set up remote API keys"
    echo "  -a, --auto-mode       Toggle auto-mode: blind-trust → classifier → off"
    echo "  -t, --telemetry       Toggle telemetry: OFF — no nonessential outbound traffic"
    echo "  -c, --choose-effort   Choose effort level for the selected model"
    echo "  --temperature VALUE   Set temperature (0.0-2.0, controls randomness/creativity)"
    echo "  --show-broken         Show unworking models (config/broken-nvidia-models.json) in picker (default: hidden; menu key u)"
    echo "  --show-old            Show catalog models over the age cutoff in picker (default: hidden; LA_REMOTE_MAX_MODEL_AGE_MONTHS, default 18 months; filter menu key o)"
    echo "  --enable-mcp          Enable MCPs for this free-API session (sets LA_REMOTE_ENABLE_MCP=1)"
    echo "  --print-spoof         Print the spoofed model id claude will be launched with, then exit"
}

_valid_model() {
    [[ "$1" =~ ^[a-zA-Z0-9@][a-zA-Z0-9_./:@+-]*$ && "$1" != SELECT ]] || {
        echo 'remote-session: provide a model ID using --remote-model (letters, digits, _ . / : @ + - only).' >&2
        return 2
    }
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
_visible() { # tier filter: hide trials and paid_only unless asked
    local tier="$1"
    [[ "$tier" != "trial" ]] || [[ $INCLUDE_TRIALS -eq 1 ]] || return 1
    [[ "$tier" != "paid_only" ]] || [[ $SHOW_PAID_ONLY_MODELS -eq 1 ]] || return 1
    return 0
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
# Collect the HIDDEN "provider|model_id" keys from _POLICY_JSON into one newline-wrapped
# string. Not an associative array: macOS /bin/bash 3.2 has no `declare -A`, and the only
# question ever asked is "is this key hidden?" (unlisted keys stay visible).
_LC_HIDDEN=$'\n'
_lc_load_policy() {
    _LC_HIDDEN=$'\n'
    if [[ -z "${_POLICY_JSON:-}" ]]; then return; fi
    # ONE python call for the whole policy, emitting "provider|model_id<TAB>true|false" per row.
    # It used to spawn four python processes per row (177 on the 0.22 roster), which cost
    # 3-5 s on EVERY invocation, --help included, while Terminal still showed the launch line.
    local key v
    while IFS=$'\t' read -r key v; do
        [[ -n "$key" && "$v" == "false" ]] && _LC_HIDDEN+="$key"$'\n'
    done <<< "$(printf '%s' "$_POLICY_JSON" | python3 -c 'import json,sys
for r in json.loads(sys.stdin.read()).get("rows",[]):
    print("%s|%s\t%s" % (r.get("provider",""), r.get("remote_model_id",""), "true" if r.get("visible",True) else "false"))' 2>/dev/null)"
}
_lc_is_hidden() {
    # $1=provider $2=model_id -> 0 if hidden, 1 if visible
    # The quoted key matches literally, so '|' '/' '.' in model ids are not glob syntax.
    [[ "$_LC_HIDDEN" == *$'\n'"${1}|${2}"$'\n'* ]]
}

# _broken_aliases -> prints the aliases listed in config/broken-nvidia-models.json, one per
# line. Fail-open: a missing or unparseable file hides nothing, so a bad edit to the list can
# never empty the picker. LA_BROKEN_MODELS_FILE overrides the path (tests use it).
_broken_aliases() {
    local f="${LA_BROKEN_MODELS_FILE:-$SCRIPT_DIR/../config/broken-nvidia-models.json}"
    [[ -r "$f" ]] || return 0
    python3 -c 'import json,sys
for m in json.load(open(sys.argv[1])).get("models",[]):
    if m.get("status","broken")=="broken" and m.get("alias"): print(m["alias"])' "$f" 2>/dev/null || true
}

# _is_broken_hidden <alias> -> 0 when the alias is listed broken AND broken models are hidden.
# The file is read once per process (cached); SHOW_BROKEN_MODELS is re-read every call so the
# menu's u toggle takes effect on the next render. Newline-wrapped so matching is exact.
_BROKEN_SET=""
_is_broken_hidden() {
    [[ $SHOW_BROKEN_MODELS -eq 0 ]] || return 1
    [[ -n "$_BROKEN_SET" ]] || _BROKEN_SET=$'\n'"$(_broken_aliases)"$'\n'
    [[ "$_BROKEN_SET" == *$'\n'"$1"$'\n'* ]]
}

# _filtered_aliases -> prints alias names, one per line, filtered by tier + local-capable + broken.
_filtered_aliases() {
    local e alias prov model disp tier
    for e in "${LA_REMOTE_AGENTS[@]}"; do
        alias="$(_field "$e" 1)"; prov="$(_field "$e" 2)"; model="$(_field "$e" 3)"
        disp="$(_field "$e" 4)"; tier="$(_field "$e" 5)"
        _visible "$tier" || continue
        if [[ $LOCAL_CAPABLE_SHOWN -eq 0 ]]; then
            _lc_is_hidden "$prov" "$model" && continue
        fi
        # Hide models listed broken in config/broken-nvidia-models.json unless --show-broken / B.
        _is_broken_hidden "$alias" && continue
        echo "$alias"
    done
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

# _sync_state -> writes current state variables to the nav file with a "stay" navigation target.
# Used when returning a selection (not navigating away) so the caller gets updated toggles.
# The "stay" target tells the caller to remain in the remote lane with updated state.
_sync_state() {
    local navfile="${CSL_NAV_FILE:-/tmp/_csl_nav.$$}"
    {
        echo "stay"
        # Sync state variables that the parent (csl or standalone) needs to know about
        [[ -n "${AUTO_MODE_STATE:-}" ]] && echo "AUTO_MODE_STATE=$AUTO_MODE_STATE"
        [[ -n "${LOCAL_CAPABLE_SHOWN:-}" ]] && echo "LOCAL_CAPABLE=$LOCAL_CAPABLE_SHOWN"
        [[ -n "${TELEMETRY_ENABLED:-}" ]] && echo "TELEMETRY=$TELEMETRY_ENABLED"
        [[ -n "${INCLUDE_TRIALS:-}" ]] && echo "INCLUDE_TRIALS=$INCLUDE_TRIALS"
        [[ -n "${EFFORT_CHOICE:-}" ]] && echo "EFFORT_CHOICE=$EFFORT_CHOICE"
        [[ -n "${SHOW_PAID_ONLY_MODELS:-}" ]] && echo "SHOW_PAID_ONLY_MODELS=$SHOW_PAID_ONLY_MODELS"
    } > "$navfile"
}

# print_inventory -> the WHOLE roster for the session picker, one TAB-separated row each:
#   alias  provider  display  tier  local_capable(0|1)  has_key(0|1)  broken(0|1)
# Unfiltered on purpose: the picker applies the trial / local-capable visibility toggles
# itself, so flipping one never needs a re-run. Reads key PRESENCE only, never a secret.
print_inventory() {
    local e alias prov model disp tier lc key br
    # Broken status is reported regardless of SHOW_BROKEN_MODELS (the picker owns the toggle).
    local SHOW_BROKEN_MODELS=0
    for e in "${LA_REMOTE_AGENTS[@]}"; do
        alias="$(_field "$e" 1)"; prov="$(_field "$e" 2)"; model="$(_field "$e" 3)"
        disp="$(_field "$e" 4)"; tier="$(_field "$e" 5)"
        lc=0; _lc_is_hidden "$prov" "$model" && lc=1
        key=0; "$KEYS" --check "$prov" >/dev/null 2>&1 && key=1
        br=0; _is_broken_hidden "$alias" && br=1
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$alias" "$prov" "$disp" "$tier" "$lc" "$key" "$br" "$model"
    done
}

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
        # session picker) — --list is a display surface too, so it must not show a
        # local-capable model by default just because it bypasses the interactive picker.
        if [[ $LOCAL_CAPABLE_SHOWN -eq 0 ]] && _lc_is_hidden "$prov" "$model"; then
            hidden_count=$((hidden_count+1))
            continue
        fi
        _is_broken_hidden "$alias" && continue
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

# _catalog_request is provided by the sourced remote-provider-registry.sh

catalog_models() { # tab-separated model ID and metadata; no default selection
    local body
    body="$(_catalog_request "$1")" || return $?
    printf '%s' "$body" | python3 "$SCRIPT_DIR/remote-catalog-parse.py"
}

choose_model() {
    local prov="$1" rows line n i=0
    local -a models=()
    if [[ "$prov" == modelscope ]]; then
        echo 'Choose an API-Inference model from https://modelscope.cn/docs/model-service/API-Inference/intro .' >&2
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

# The swap pre-flight's scratch port: a band PAST the live session range (user 2026-10-24,
# Phase-4 dogfood: the scratch landed on 4144, inside 4141-4151, where the next live session
# could pick it). A rescue must never occupy a port a live session may be about to launch on.
swap_scratch_port() {
    local p
    for ((p = LA_REMOTE_PROXY_PORT_MAX + 1; p <= LA_REMOTE_PROXY_PORT_MAX + 20; p++)); do
        if ! lsof -ti tcp:"$p" >/dev/null 2>&1; then echo "$p"; return 0; fi
    done
    echo "remote-session: no free scratch port in ${LA_REMOTE_PROXY_PORT_MAX}+1..+20" >&2
    return 1
}

free_port() {
    local p
    for ((p=LA_REMOTE_PROXY_PORT_MIN; p<=LA_REMOTE_PROXY_PORT_MAX; p++)); do
        # A port is NOT free while the session that owned it still runs, even if its proxy died:
        # that session's ANTHROPIC_BASE_URL still points here, and a new proxy for another
        # provider on this port would silently answer it (the 2026-10-20 Mistral misroute).
        if [[ -r "$RUNDIR/proxy-$p.owner" ]] && _owner_alive "$(cat "$RUNDIR/proxy-$p.owner")"; then
            continue
        fi
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
    rm -f "$pf" "$RUNDIR/proxy-$port.owner"
    return 0
}

_proc_start() { # _proc_start <pid> -> the process's start time, compact; empty if it is gone
    ps -o lstart= -p "$1" 2>/dev/null | tr -s ' ' '_'
}

_owner_alive() { # _owner_alive "<pid> <start>" -> 0 only if THAT process (not a recycled pid) runs
    local pid="${1%% *}" start="${1#* }"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null || return 1
    # Hours after a session ends macOS may reuse its pid for an unrelated process; the start time
    # tells them apart. An owner line without a start time (pid only) falls back to kill -0.
    [[ "$start" == "$1" || -z "$start" ]] && return 0
    [[ "$(_proc_start "$pid")" == "$start" ]]
}

stop_proxy() { # stop_proxy [<port>] [--force]
    # INCIDENT 2026-10-20 00:38 UTC: a TESTING session ran `--stop`, which stopped every proxy of
    # ours -- including the one serving a healthy all-night NVIDIA session in another terminal. The
    # next probe reused the freed port with a Mistral config, so that session's requests went to
    # Mistral and died on Mistral's 429. A proxy belongs to the session that launched it
    # (proxy-<port>.owner = the launcher's pid), so:
    #   --stop              stops only ORPHANS: owner recorded and no longer running
    #   --stop <port>       that one proxy, and it too refuses while its owner is alive
    #   --stop <port> --force   the explicit override
    # A proxy with NO owner record (started by an older launcher) is kept: unknown is not orphaned.
    local want="" force=0 a killed=0 kept=0 pf port pid owner
    for a in "$@"; do
        case "$a" in --force) force=1 ;; *) want="$a" ;; esac
    done
    # nullglob: an unmatched glob would otherwise be iterated as a literal filename.
    shopt -s nullglob
    for pf in "$RUNDIR"/proxy-*.pid; do
        port="$(basename "$pf")"; port="${port#proxy-}"; port="${port%.pid}"
        [[ -n "$want" && "$port" != "$want" ]] && continue
        pid="$(cat "$pf" 2>/dev/null)"
        owner="$(cat "$RUNDIR/proxy-$port.owner" 2>/dev/null)"
        if ! _is_our_proxy "$pid" "$port"; then
            rm -f "$pf" "$RUNDIR/proxy-$port.owner"; continue
        fi
        if [[ $force -eq 0 ]]; then
            if [[ -z "$owner" ]]; then
                echo "remote-session: kept proxy on port $port: no owner record, so it may serve a live session" \
                     "(stop it explicitly: $0 --stop $port --force)" >&2
                kept=$((kept+1)); continue
            fi
            if _owner_alive "$owner"; then
                echo "remote-session: kept proxy on port $port: its session (pid ${owner%% *}) is still running" \
                     "(override: $0 --stop $port --force)" >&2
                kept=$((kept+1)); continue
            fi
        fi
        kill "$pid" 2>/dev/null && killed=$((killed+1))
        echo "remote-session: stopped proxy pid $pid (port $port)"
        rm -f "$pf" "$RUNDIR/proxy-$port.owner"
    done
    shopt -u nullglob
    [[ $killed -eq 0 && $kept -eq 0 ]] && echo "remote-session: no proxy of ours was running"
    return 0
}

# LiteLLM maps each spoofed Claude id onto the chosen remote model, so Claude
# Code can ask for "claude-opus-5" and get the free provider underneath.
# SUBAGENTS (0.27.0). Claude Code's Agent/Workflow subagents ask for whatever claude-* id their
# definition names (claude-haiku-5-5, claude-sonnet-4-6, …), not only the spoofed session ids, and
# the proxy answered every unknown id with "400 Invalid model name" — so a subagent in a free-API
# session always failed. A "claude-*" wildcard group now catches every other Claude id.
#   LA_SUBAGENT_ROUTING=same    (default) the group holds only the session's own model
#   LA_SUBAGENT_ROUTING=hybrid  the group ALSO holds each LA_SUBAGENT_POOL member, and LiteLLM
#                               spreads subagent calls across them: the council idea, several
#                               different models answering in parallel
#   LA_SUBAGENT_POOL="provider:model,…"  e.g. "nvidia:poolside/laguna-xs-2.1,gemini:gemini-3.8-flash"
#   LA_SUBAGENT_POOL_TIMEOUT=120         seconds before a pool member counts as failed (then retried)
# Members whose key is missing are skipped with a note. NVIDIA members still pass the machine-wide
# limiter (la_proxy_hooks), so parallel subagents queue instead of spending the session's 40 RPM.
# CLASSIFIER (0.28.0). Auto mode's classifier requests claude-sonnet-5[1m]. Without an entry it lands
# on the "claude-*" catch-all (the session model) -- which works, measured. LA_CLASSIFIER_MODEL
# ("provider:model", e.g. "nvidia:nvidia/nemotron-3-super-120b-a12b") gives it a DEDICATED, usually
# faster model, so stage-2 (measured 85s on Ultra) keeps headroom under Claude Code's 120s cap.
# Written FIRST: an exact name beats the wildcard. Thinking off (the verdict is XML, not prose).
CLASSIFIER_DEDICATED=0
_write_classifier_entry() { # _write_classifier_entry <cfg> <session-provider> <session-key-env>
    local cfg="$1" sprov="$2" member="${LA_CLASSIFIER_MODEL:-}" cprov cmodel route ckey name
    CLASSIFIER_DEDICATED=0
    [[ -n "$member" ]] || return 0
    cprov="${member%%:*}"; cmodel="${member#*:}"
    if [[ "$cprov" == "$member" || -z "$cmodel" ]] || ! _valid_model "$cmodel" 2>/dev/null; then
        echo "remote-session: LA_CLASSIFIER_MODEL '$member' is not provider:model; using the session model" >&2
        return 0
    fi
    route="$(_litellm_route "$cprov" "$cmodel")" || {
        echo "remote-session: LA_CLASSIFIER_MODEL provider '$cprov' has no route; using the session model" >&2; return 0; }
    "$KEYS" --check "$cprov" >/dev/null 2>&1 || {
        echo "remote-session: LA_CLASSIFIER_MODEL skipped (no ~/.api_keys/$cprov); using the session model" >&2; return 0; }
    ckey="$("$KEYS" --names "$cprov")" || return 0; ckey="${ckey%%$'\n'*}"
    for name in claude-sonnet-5 'claude-sonnet-5[1m]'; do
        {
            echo "  - model_name: \"$name\""
            echo "    litellm_params:"
            echo "      model: ${route%%|*}"
            echo "      api_key: os.environ/$ckey"
            [[ -n "${route#*|}" ]] && echo "      api_base: ${route#*|}"
            [[ "$cprov" == nvidia && ( "$cmodel" == *nemotron* || "$cmodel" == *glm* ) ]] && \
                printf '      chat_template_kwargs:\n        enable_thinking: false\n'
            echo "      num_retries: 2"
        } >> "$cfg"
    done
    CLASSIFIER_DEDICATED=1
    [[ "$cprov" != "$sprov" && " $SUBAGENT_POOL_PROVIDERS " != *" $cprov "* ]] && \
        SUBAGENT_POOL_PROVIDERS="${SUBAGENT_POOL_PROVIDERS:+$SUBAGENT_POOL_PROVIDERS }$cprov"
    return 0
}

SUBAGENT_POOL_PROVIDERS=""   # extra providers whose keys the proxy needs (set by _write_subagent_pool)
_write_subagent_pool() { # _write_subagent_pool <cfg> <session-provider>  (hybrid members only)
    local cfg="$1" prov="$2"
    [[ "${LA_SUBAGENT_ROUTING:-same}" == hybrid ]] || return 0
    local member mprov mmodel route mkey
    IFS=',' read -r -a _members <<< "${LA_SUBAGENT_POOL:-}"
    for member in "${_members[@]}"; do
        member="${member// /}"; [[ -n "$member" ]] || continue
        mprov="${member%%:*}"; mmodel="${member#*:}"
        if [[ "$mprov" == "$member" || -z "$mmodel" ]] || ! _valid_model "$mmodel" 2>/dev/null; then
            echo "remote-session: LA_SUBAGENT_POOL member '$member' is not provider:model; skipped" >&2
            continue
        fi
        route="$(_litellm_route "$mprov" "$mmodel")" || {
            echo "remote-session: LA_SUBAGENT_POOL provider '$mprov' has no route; skipped" >&2; continue; }
        if ! "$KEYS" --check "$mprov" >/dev/null 2>&1; then
            echo "remote-session: LA_SUBAGENT_POOL member $member skipped (no ~/.api_keys/$mprov)" >&2
            continue
        fi
        mkey="$("$KEYS" --names "$mprov")" || continue; mkey="${mkey%%$'\n'*}"
        {
            echo '  - model_name: "claude-*"'
            echo "    litellm_params:"
            echo "      model: ${route%%|*}"
            echo "      api_key: os.environ/$mkey"
            [[ -n "${route#*|}" ]] && echo "      api_base: ${route#*|}"
            [[ "$mprov" == nvidia && ( "$mmodel" == *nemotron* || "$mmodel" == *glm* ) ]] && \
                printf '      chat_template_kwargs:\n        enable_thinking: false\n'
            # A pool member that hangs (measured: a provider giving no byte for 60s) must not
            # stall the subagent: time it out, and the router's retry lands on another member.
            echo "      timeout: ${LA_SUBAGENT_POOL_TIMEOUT:-120}"
            echo "      num_retries: 1"
        } >> "$cfg"
        [[ "$mprov" != "$prov" && " $SUBAGENT_POOL_PROVIDERS " != *" $mprov "* ]] && \
            SUBAGENT_POOL_PROVIDERS="${SUBAGENT_POOL_PROVIDERS:+$SUBAGENT_POOL_PROVIDERS }$mprov"
    done
    return 0
}

# Per-model request params (thinking/effort lines) for one deployment, shared by the session model
# and its fallbacks so both get the same model-family rules. Sets MP_THINK_LINE / MP_EFFORT_LINE.
MP_THINK_LINE=""; MP_EFFORT_LINE=""
_model_param_lines() { # _model_param_lines <provider> <model> <thinking> [effort]
    local prov="$1" model="$2" thinking="$3" effort="${4:-}" think_line=""
    # Gemini 3.x spends its whole token budget on thinking unless told not to;
    # that truncated real replies during bring-up, so default it OFF.
    [[ "$prov" == "gemini" && "$thinking" != "true" ]] && \
        think_line='      thinking: {"type": "disabled"}'
    # NVIDIA NIM reasoning models (Nemotron) put their reasoning in
    # `reasoning_content`, which is NOT an Anthropic thinking block -- the
    # translation layer then dies with "Content block is not a thinking block"
    # and takes the whole session's endpoint with it. Turn reasoning off at the
    # backend unless the caller explicitly asked for thinking.
    # ONLY for Nemotron models; other NVIDIA models (Kimi, DeepSeek, GLM) have
    # different behavior and this setting breaks them or does nothing useful.
    # EXPLICIT CONTROL: the -thinking suffix means enable_thinking: true,
    # the default (no suffix) means enable_thinking: false. Effort does NOT
    # change this - it only sets reasoning_effort for non-Nemotron models.
    if [[ "$prov" == "nvidia" && "$model" == *nemotron* ]]; then
        if [[ "$thinking" == "true" ]]; then
            think_line='      chat_template_kwargs:
        enable_thinking: true'
        else
            think_line='      chat_template_kwargs:
        enable_thinking: false'
        fi
    fi
    # GLM on NIM: enable_thinking is INERT -- measured 2026-10-13 with a 4-shape
    # controlled test (enable_thinking: false, {"type":"disabled"} dict, boolean false,
    # absent): reasoning fires identically in all four, so the -thinking suffix used to
    # differ from the base alias only by a kwarg that did nothing. GLM's thinking dial
    # is the API-layer reasoning_effort (low -> 0 reasoning tokens), set in the effort
    # block below. No chat_template_kwargs for GLM. See docs/nvidia-nim-serving-notes.md.
    # (The old 'needs enable_thinking: true' note was a misattribution: the same v0.21.4
    # fix also raised max_tokens, which is what did the work.)

    # EFFORT. Claude Code's own --effort flag is meaningless to a third-party provider: it is
    # interpreted by Anthropic's models, so passing it to `claude` while the request is proxied
    # to NVIDIA/Gemini/Groq changed nothing. Effort has to travel IN THE REQUEST BODY, which is
    # what this proxy config controls. The field differs per model family, so map it:
    #   * reasoning_effort (low|medium|high) -- the OpenAI-compatible spelling. LiteLLM
    #     translates it per provider, so it is the right default for OpenAI-compatible routes.
    #   * NVIDIA Nemotron does NOT take reasoning_effort; it gates reasoning with
    #     enable_thinking inside chat_template_kwargs, controlled by the -thinking suffix,
    #     not by effort. So effort has no request-body field to travel in for Nemotron.
    #     Other NVIDIA models DO accept reasoning_effort.
    # Claude Code offers five levels; xhigh/max fold to high for reasoning_effort (OpenAI
    # only accepts low/medium/high).
    # NOT max_tokens. 0.21.5 mapped effort to a per-deployment max_tokens here; it never took
    # effect, because Claude Code always sends max_tokens and the request's value overrides the
    # deployment's. The output ceiling is CLAUDE_CODE_MAX_OUTPUT_TOKENS (MAX_OUT above), and it
    # is the maximum for every effort: a ceiling only truncates, it never buys deeper reasoning.
    local mapped_effort=""
    case "$effort" in
        low)              mapped_effort="low" ;;
        medium)           mapped_effort="medium" ;;
        high)             mapped_effort="high" ;;
        xhigh)            mapped_effort="xhigh" ;;
        max)              mapped_effort="max" ;;
        "")               mapped_effort="" ;;
        *)                echo "remote-session: unknown effort '\''$effort'\'', ignoring" >&2 ;;
    esac
    # GLM on NIM has no chat_template thinking control (inert in every shape, measured
    # 2026-10-13), so the -thinking suffix must express itself through reasoning_effort:
    # default high for -thinking, low for the base alias (low -> 0 reasoning tokens on
    # NIM, verified direct). An explicit --effort still wins.
    if [[ "$prov" == "nvidia" && "$model" == *glm* && -z "$mapped_effort" ]]; then
        if [[ "$thinking" == "true" ]]; then
            mapped_effort="high"
        else
            mapped_effort="low"
        fi
    fi
    local effort_line=""
    if [[ -n "$mapped_effort" ]]; then
        # Map xhigh/max to high for reasoning_effort (OpenAI only accepts low/medium/high)
        local reasoning_effort="$mapped_effort"
        case "$mapped_effort" in
            xhigh|max) reasoning_effort="high" ;;
        esac
        case "$prov" in
            nvidia)
                # Nemotron reads enable_thinking, not reasoning_effort.
                # Other NVIDIA models DO accept reasoning_effort.
                if [[ "$model" != *nemotron* ]]; then
                    effort_line="      reasoning_effort: $reasoning_effort"
                fi
                ;;
            gemini)
                # Gemini thinking is disabled above unless asked for; an explicit effort asks.
                [[ "$thinking" != "true" ]] && think_line=""
                effort_line="      reasoning_effort: $reasoning_effort"
                ;;
            *)  effort_line="      reasoning_effort: $reasoning_effort" ;;
        esac
    fi

    MP_THINK_LINE="$think_line"; MP_EFFORT_LINE="$effort_line"
}

# FALLBACK CHAIN (0.40.0). NVIDIA can throttle ONE model upstream for hours (GLM 5.3 Flash
# answered 429 in 0.2 s all night on 2026-10-20 while DeepSeek on the same key answered 46/46).
# The proxy then answers from the next model in the session chain (bin/session-fallbacks.py:
# config/dispatch-preferences.json session_fallbacks, same provider only, own model skipped), on
# the same port, with no restart. Members without a key are skipped. LA_REMOTE_FALLBACKS=off.
#
# ACCOUNT ERRORS are why the chain is SAME-PROVIDER ONLY (measured, contract test 3 with
# fallbacks): LiteLLM 1.105's generic fallback path is exception-agnostic, so nothing stops a
# fallback from answering after ANY failure. An account refusal (insufficient balance, quota
# exhausted) is KEY-WIDE, and same-provider means same-key: every chain member refuses
# identically, the chain exhausts within one request, and la_proxy_hooks' 402 rewrite reaches
# the client. A cross-provider member would drain another account and PAPER OVER the refusal.
_session_fallbacks() { # _session_fallbacks <provider> <model> -> "provider:model" lines (keyed)
    local line fprov fmodel
    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        fprov="${line%%:*}"; fmodel="${line#*:}"
        _valid_model "$fmodel" 2>/dev/null || continue
        "$KEYS" --check "$fprov" >/dev/null 2>&1 || continue
        echo "$line"
    done < <(python3 "$SCRIPT_DIR/session-fallbacks.py" "$1" "$2" 2>/dev/null)
}

FALLBACK_NAMES=()
_write_fallbacks() { # _write_fallbacks <cfg> <provider> <model> <thinking> [effort] [temperature]
    local cfg="$1" prov="$2" model="$3" thinking="$4" effort="${5:-}" temperature="${6:-}"
    local line fprov fmodel route key n=0
    FALLBACK_NAMES=()
    while IFS= read -r line; do
        fprov="${line%%:*}"; fmodel="${line#*:}"
        route="$(_litellm_route "$fprov" "$fmodel")" || continue
        key="$("$KEYS" --names "$fprov")" || continue; key="${key%%$'\n'*}"
        _model_param_lines "$fprov" "$fmodel" "$thinking" "$effort"
        n=$((n+1)); FALLBACK_NAMES+=("la-fallback-$n")
        {
            echo "  - model_name: \"la-fallback-$n\""
            echo "    litellm_params:"
            echo "      model: ${route%%|*}"
            echo "      api_key: os.environ/$key"
            [[ -n "${route#*|}" ]] && echo "      api_base: ${route#*|}"
            [[ -n "$MP_THINK_LINE" ]] && echo "$MP_THINK_LINE"
            [[ -n "$MP_EFFORT_LINE" ]] && echo "$MP_EFFORT_LINE"
            [[ -n "$temperature" ]] && echo "      temperature: $temperature"
            echo "      num_retries: 1"
        } >> "$cfg"
    done < <(_session_fallbacks "$prov" "$model")
    return 0
}

write_proxy_config() { # write_proxy_config <cfgpath> <provider> <model> <thinking> [effort] [temperature]
    local cfg="$1" prov="$2" model="$3" thinking="$4" effort="${5:-}" temperature="${6:-}"
    _available "$prov" || return 2
    _valid_model "$model" || return 2
    local litellm_model think_line="" api_base=""
    # The route comes from the generated registry (config/remote-registry.yaml), so a provider
    # added there is routable here without a second hand-kept list.
    local route
    route="$(_litellm_route "$prov" "$model")" || {
        echo "remote-session: no LiteLLM prefix for provider $prov" >&2; return 1; }
    litellm_model="${route%%|*}"; api_base="${route#*|}"
    _model_param_lines "$prov" "$model" "$thinking" "$effort"
    think_line="$MP_THINK_LINE"; local effort_line="$MP_EFFORT_LINE"

    local key_env
    key_env="$("$KEYS" --names "$prov")" || return 1
    key_env="${key_env%%$'\n'*}"

    # NATIVE ANTHROPIC LANE (0.38.1). When start_proxy decided (bin/anthropic-route.py, one cached
    # probe per model) that this provider serves THIS model on its own Anthropic /v1/messages, the
    # proxy forwards Claude Code's request untranslated: thinking, output_config.effort and
    # cache_control arrive as written, so effort is the model's own Anthropic effort rather than
    # our reasoning_effort mapping. LiteLLM's passthrough (supported_endpoints) still runs our
    # hooks; la_proxy_hooks registers the model as reasoning-capable (LA_PASSTHROUGH_MODELS),
    # without which LiteLLM silently DROPS thinking/effort for an id it does not know.
    local native_line=""
    if [[ "${LA_ROUTE_NATIVE:-0}" == 1 ]] && api_base="$(_anthropic_base "$prov")"; then
        litellm_model="openai/$model"
        native_line='    model_info:
      supported_endpoints: ["/v1/messages"]'
        think_line=""; effort_line=""
        export LA_PASSTHROUGH_MODELS="$model"
    fi

    _prepare_runtime_file "$cfg" || return 1
    echo "model_list:" >> "$cfg"
    local spoof
    IFS=',' read -r -a _spoofs <<< "$LA_REMOTE_SPOOF_IDS"
    SUBAGENT_POOL_PROVIDERS=""
    _write_classifier_entry "$cfg" "$prov" "$key_env" || return 1
    # The exact spoof ids first, then the "claude-*" catch-all for subagent ids (same params). A
    # dedicated classifier owns claude-sonnet-5*, so the session group must not ALSO claim it:
    # two groups under one name are load-balanced by LiteLLM, not prioritised.
    for spoof in "${_spoofs[@]}" '"claude-*"'; do
        [[ "$CLASSIFIER_DEDICATED" == 1 && "$spoof" == claude-sonnet-5* ]] && continue
        {
            echo "  - model_name: $spoof"
            echo "    litellm_params:"
            echo "      model: $litellm_model"
            echo "      api_key: os.environ/$key_env"
            [[ -n "$api_base" ]] && echo "      api_base: $api_base"
            [[ -n "$think_line" ]] && echo "$think_line"
            [[ -n "$effort_line" ]] && echo "$effort_line"
            # No max_tokens here: Claude Code always sends one and it overrides this (see MAX_OUT).
            # Temperature control (if provided)
            if [[ -n "$temperature" ]]; then
                echo "      temperature: $temperature"
            fi
            # Retry configuration for NVIDIA (and other free-tier providers)
            # num_retries: retry failed upstream calls instead of returning 500 immediately
            # Note: retry_after is NOT supported by NVIDIA NIM (causes 400 BadRequestError)
            if [[ "$prov" == "nvidia" || "$prov" == "gemini" || "$prov" == "groq" ]]; then
                echo "      num_retries: 3"
            fi
            [[ -n "$native_line" ]] && echo "$native_line"
        } >> "$cfg"
    done
    _write_fallbacks "$cfg" "$prov" "$model" "$thinking" "$effort" "$temperature" || return 1
    _write_subagent_pool "$cfg" "$prov" || return 1
    cat >> "$cfg" <<'YAML'
litellm_settings:
  drop_params: true
  # Never bridge Anthropic /v1/messages through the OpenAI Responses API: LiteLLM sends EVERY
  # openai/ route there by default, and zai, siliconflow, llm7, ... 404 on /responses. The proxy
  # applies these keys with setattr(litellm, key, value), so a misspelt or retired key is SILENTLY
  # inert: until 0.38.1 this line read `disable_responses_api: true`, a key LiteLLM never had, and
  # every openai/ route failed its first prompt. (`telemetry: false` went for the same reason: a
  # legacy no-op since LiteLLM 1.105.) tests/test_remote_session.py checks each key against the
  # installed LiteLLM.
  use_chat_completions_url_for_anthropic_messages: true
  # Gemini: Claude Code marks its prompt with cache_control, which LiteLLM turns into a paid
  # cachedContents call that the free tier answers with 429 before the real request is sent.
  disable_anthropic_gemini_context_caching_transform: true
YAML
    # PROXY HOOKS (bin/la_proxy_hooks.py): NIM's rejected request keys (safeguards, and
    # stop_sequences on LiteLLM < 1.102.1 -> HTTP 400), the machine-wide NVIDIA 40 RPM bucket,
    # and a load-time warning if LiteLLM still has the "not a thinking block" stream bug. LiteLLM loads a
    # callback module RELATIVE TO THE CONFIG FILE, so the module is linked next to it.
    # LA_REMOTE_PROXY_HOOKS=0 turns this off, loudly, for A/B-ing a regression.
    if [[ "${LA_REMOTE_PROXY_HOOKS:-1}" != "0" && -r "$SCRIPT_DIR/la_proxy_hooks.py" ]]; then
        ln -sf "$SCRIPT_DIR/la_proxy_hooks.py" "$(dirname "$cfg")/la_proxy_hooks.py" || return 1
        echo "  callbacks: [la_proxy_hooks.proxy_hooks]" >> "$cfg"
    elif [[ "${LA_REMOTE_PROXY_HOOKS:-1}" == "0" ]]; then
        echo "remote-session: NOTE proxy hooks DISABLED (LA_REMOTE_PROXY_HOOKS=0) — no NVIDIA" >&2
        echo "  rate limiting, no NIM parameter fixes, no LiteLLM stream-bug check." >&2
    fi
    # ONE retry on HTTP 400, so a provider that rejects the output ceiling (Groq: "`max_tokens`
    # must be less than or equal to `16384`") is retried at the cap la_proxy_hooks.py just
    # learned from that error, instead of the turn failing. Any other 400 costs one extra call.
    cat >> "$cfg" <<'YAML'
router_settings:
  retry_policy:
    BadRequestErrorRetries: 1
YAML
    if [[ ${#FALLBACK_NAMES[@]} -gt 0 ]]; then
        # A throttled model fails over AT ONCE instead of retrying itself; every spoofed id and the
        # generic "*" key list the chain ("claude-*" as a key does NOT match a subagent id such as
        # claude-haiku-9: measured on LiteLLM 1.105.1, the generic "*" does).
        local chain="" fid entries=""
        for fid in "${FALLBACK_NAMES[@]}"; do chain+="${chain:+, }\"$fid\""; done
        for fid in "${_spoofs[@]}" '*'; do entries+="${entries:+, }{\"$fid\": [$chain]}"; done
        { echo "    RateLimitErrorRetries: 0"; echo "  fallbacks: [$entries]"; } >> "$cfg"
    fi
    cat >> "$cfg" <<'YAML'
  # One deployment per name: a cooldown after its first failure answers every retry with a
  # synthetic "429 No deployments available ... Try again in 5 seconds", which Claude Code counts
  # down and retries for minutes while the REAL upstream error (404, 402 balance, 400) stays hidden.
  disable_cooldowns: true
general_settings:
  master_key: sk-local-agents-remote
YAML
}

_decide_native_route() { # _decide_native_route <provider> <model> -> sets LA_ROUTE_NATIVE=0|1
    # Per MODEL, not per provider: LLM7 serves some ids natively and refuses others. One tiny probe,
    # cached 30 days (bin/anthropic-route.py); an undecided probe (balance, rate, 5xx) is not
    # cached and falls back to chat/completions. LA_REMOTE_ANTHROPIC=on|off forces it.
    LA_ROUTE_NATIVE=0
    local base verdict key_line
    base="$(_anthropic_base "$1")" || return 0
    key_line="$("$KEYS" --env "$1" 2>/dev/null | head -1)"
    # LiteLLM's own interpreter, not bare python3: it carries truststore, which the PYTHONPATH
    # shim injects so the probe trusts the macOS Keychain. Under a system python3 without it,
    # a TLS-inspecting network fails the probe (CERTIFICATE_VERIFY_FAILED, measured on corporate
    # wifi 2026-10-20) and every model reads "undecided" -- the native lane silently never wins.
    local py; py="$(head -1 "$(command -v litellm 2>/dev/null)" 2>/dev/null)"; py="${py#\#!}"; py="${py%% *}"
    # Only a PYTHON interpreter will do: a non-console-script litellm (a wrapper, a test stub)
    # has a shell shebang, and running the probe under /bin/sh fails it silently -> "chat".
    [[ -x "$py" && "$(basename "$py")" == [Pp]ython* ]] || py=python3
    verdict="$(LA_ANTHROPIC_KEY="${key_line#*=}" "$py" "$SCRIPT_DIR/anthropic-route.py" \
               decide "$1" "$2" "$base")" || verdict="chat"
    if [[ "$verdict" == anthropic ]]; then
        LA_ROUTE_NATIVE=1
    fi
    return 0
}

_spawn_proxy_on() { # _spawn_proxy_on <port> <cfg> <log> <prov> -> background pid on stdout
    # The ONE spawn path (0.40.0): start_proxy, the swap pre-flight and the swap's revert
    # all launch through here, so a swapped session runs exactly what a fresh one would.
    # Only THIS provider's credential (plus the subagent pool's) enters the proxy env.
    local port="$1" cfg="$2" log="$3" prov="$4"
    local -a cmd=()
    if [[ -n "${LA_LITELLM_CMD:-}" ]]; then
        # shellcheck disable=SC2206
        local -a _c=(${LA_LITELLM_CMD})
        command -v "${_c[0]}" >/dev/null 2>&1 || [[ -x "${_c[0]}" ]] || {
            echo "remote-session: LA_LITELLM_CMD is not executable: ${_c[0]}" >&2; return 1; }
        echo "remote-session: NOTE using LA_LITELLM_CMD (${_c[0]}) — the OS-trust wrapper is BYPASSED;" >&2
        echo "  on a TLS-inspecting network upstream HTTPS may fail with a certificate error." >&2
        cmd=("${_c[@]}")
    else
        # Resolve the interpreter behind the console script and re-enter it WITHOUT the
        # shebang's `-E`: with -E Python ignores PYTHONPATH, the Keychain-trust shim never
        # reached the proxy, and on a TLS-inspecting corporate network every upstream HTTPS
        # call failed as a "self-signed certificate" 500 that reads like a provider outage.
        local litellm_py litellm_shebang trust_wrapper
        litellm_shebang="$(head -1 "$(command -v litellm)" 2>/dev/null)"
        litellm_py="${litellm_shebang#\#!}"; litellm_py="${litellm_py%% *}"
        if [[ ! -x "$litellm_py" ]]; then
            echo "remote-session: could not resolve the litellm interpreter from: ${litellm_shebang:-<none>}" >&2
            echo "  reinstall with: pipx install 'litellm[proxy]'" >&2
            return 1
        fi
        trust_wrapper="$SCRIPT_DIR/litellm-with-trust.py"
        if [[ ! -r "$trust_wrapper" ]]; then
            echo "remote-session: missing trust wrapper: $trust_wrapper" >&2
            return 1
        fi
        cmd=("$litellm_py" "$trust_wrapper")
    fi
    local envassign line _pp
    envassign="$("$KEYS" --env "$prov")" || return 1
    for _pp in $SUBAGENT_POOL_PROVIDERS; do
        envassign+=$'\n'"$("$KEYS" --env "$_pp")" || return 1
    done
    ( _clear_provider_env
      # shellcheck disable=SC2163 # line is a full NAME=VALUE string from remote-keys.sh --env; export "NAME=VALUE" is valid bash
      while IFS= read -r line; do [[ -n "$line" ]] && export "$line"; done <<< "$envassign"
      # shellcheck disable=SC2030,SC2031 # LA_REMOTE_PROVIDER is scoped to the proxy process ON PURPOSE: the parent launcher keeps its own value
      export LA_REMOTE_PROVIDER="$prov"   # la_proxy_hooks names it in account errors
      exec "${cmd[@]}" --config "$cfg" --port "$port" ) > "$log" 2>&1 &
    echo $!
}

_wait_proxy_ready() { # _wait_proxy_ready <port> <pid> <secs> -> 0 once the health endpoint answers
    local port="$1" pid="$2" secs="$3" i
    for i in $(seq 1 "$secs"); do
        if curl -s -m 2 "http://127.0.0.1:$port/health/liveliness" >/dev/null 2>&1; then
            return 0
        fi
        kill -0 "$pid" 2>/dev/null || {
            echo "remote-session: proxy died during startup; inspect the private log locally: $RUNDIR/proxy-$port.log" >&2
            return 1; }
        sleep 1
    done
    echo "remote-session: proxy did not become ready in ${secs}s; inspect the private log locally: $RUNDIR/proxy-$port.log" >&2
    return 1
}

start_proxy() { # start_proxy <provider> <model> <thinking> [effort] [temperature] -> echoes port
    local prov="$1" model="$2" thinking="$3" effort="${4:-}" temperature="${5:-}"
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
    _decide_native_route "$prov" "$model"
    write_proxy_config "$cfg" "$prov" "$model" "$thinking" "$effort" "$temperature" || return 1
    # 0.40.0: the hooks fast-fail a cooling model to this chain instead of queueing; with no
    # chain they keep waiting in the 40 RPM window exactly as before (see la_proxy_hooks.py).
    export LA_PROXY_FALLBACK_NAMES="${FALLBACK_NAMES[*]:-}"
    _prepare_runtime_file "$log" || return 1
    _prepare_runtime_file "$pidf" || return 1
    # 60s to prove a stub/test command; 300s for real proxies -- the first request's prefill
    # on a large model can take minutes before the health endpoint answers.
    local bgpid
    bgpid="$(_spawn_proxy_on "$port" "$cfg" "$log" "$prov")" || return 1
    echo "$bgpid" > "$pidf"; echo "$$ $(_proc_start $$)" > "$RUNDIR/proxy-$port.owner"
    if _wait_proxy_ready "$port" "$bgpid" "$([[ -n "${LA_LITELLM_CMD:-}" ]] && echo 60 || echo 300)"; then
        echo "$port"; return 0
    fi
    return 1
}

# ---- 0.40.0: --swap-model — rescue a session whose model is throttled upstream -----------
# Phase 1's fallback chain covers NEW sessions; this rescues one started before 0.40.0 (or
# one whose whole chain failed) by relaunching ITS proxy on another model, same port, so
# the stalled session resumes on its next prompt. The six mechanisms of the proven one-off
# (la-swap-proxy-model.py, 2026-10-20) with the config now built by write_proxy_config —
# one source of truth — and every path derived from $RUNDIR/$SCRIPT_DIR, nothing hardcoded.
LA_SWAP_HEALTHY_WINDOW="${LA_SWAP_HEALTHY_WINDOW:-600}"   # an answer this recent => NOT stalled
LA_SWAP_PROBE_TIMEOUT="${LA_SWAP_PROBE_TIMEOUT:-420}"      # above the limiter's 300s queue bound

_swap_master_key() { # _swap_master_key <cfg> -> the config's master key
    python3 - "$1" <<'PY'
import re, sys
m = re.search(r"^\s*master_key:\s*(\S+)", open(sys.argv[1]).read(), re.M)
print(m.group(1) if m else "")
PY
}

_swap_probe() { # _swap_probe <port> <cfg> <timeout> -> "HTTPCODE body-snippet"
    local port="$1" cfg="$2" tmo="$3" key out code
    key="$(_swap_master_key "$cfg")"
    [[ -n "$key" ]] || { echo "000 no-master-key-in-config"; return 1; }
    out="$RUNDIR/swap-probe-$port.out"
    code="$(curl -s -m "$tmo" -o "$out" -w '%{http_code}' \
        -H "x-api-key: $key" -H 'content-type: application/json' \
        -H 'anthropic-version: 2023-06-01' \
        -d '{"model":"claude-opus-5-5","max_tokens":16,"messages":[{"role":"user","content":"ping"}]}' \
        "http://127.0.0.1:$port/v1/messages" 2>/dev/null)" || { echo "000 curl-failed"; return 1; }
    echo "$code $(head -c 120 "$out" | tr '\n' ' ')"
    [[ "$code" == "200" ]]
}

_swap_revert() { # _swap_revert <port> <bak-cfg> <cfg> <log> <pidf> <newpid> <prov> [secs]
    # Put the OLD config back and relaunch it: readiness is verified BEFORE the pidfile is
    # written, or a dead proxy would look live to teardown and --stop.
    local port="$1" bak="$2" cfg="$3" log="$4" pidf="$5" newpid="$6" prov="$7" secs="${8:-300}" rpid
    echo "remote-session: --swap-model: reverting :$port to the previous model" >&2
    kill "$newpid" 2>/dev/null
    cp -p "$bak" "$cfg"
    rpid="$(_spawn_proxy_on "$port" "$cfg" "$log" "$prov")" || return 1
    if ! _wait_proxy_ready "$port" "$rpid" "$secs"; then
        echo "remote-session: --swap-model: the REVERTED proxy is not answering on :$port —" >&2
        echo "  the original config is restored at $cfg; start it with:" >&2
        echo "    $SCRIPT_DIR/$(basename "$0") ... (or relaunch the session)" >&2
        echo "$rpid" > "$pidf"
        return 1
    fi
    echo "$rpid" > "$pidf"
    echo "remote-session: --swap-model: reverted; the previous model is answering again on :$port" >&2
    return 0
}

swap_model() { # swap_model <port> <alias> [force]
    local port="${1:-}" alias="${2:-}" force="${3:-}"
    local cfg pidf log ownerf pid entry prov model thinking effort temperature
    [[ -n "$port" && -n "$alias" ]] || { echo "remote-session: --swap-model needs <port> <alias>" >&2; return 2; }
    _prepare_runtime_dir || return 2
    cfg="$RUNDIR/proxy-$port.yaml"; pidf="$RUNDIR/proxy-$port.pid"
    log="$RUNDIR/proxy-$port.log"; ownerf="$RUNDIR/proxy-$port.owner"
    for f in "$cfg" "$pidf" "$log"; do
        [[ -r "$f" ]] || { echo "remote-session: --swap-model: missing $f — is a session running on :$port?" >&2; return 2; }
    done
    pid="$(cat "$pidf" 2>/dev/null)"
    _is_our_proxy "$pid" "$port" || {
        echo "remote-session: --swap-model: pid ${pid:-<none>} on :$port is not one of our proxies; refusing." >&2
        return 2; }
    # HEALTHY REFUSAL: a log written inside the window means the proxy answered something
    # recently — a swap would drop an in-flight turn. Refuse unless --force.
    local log_age
    log_age=$(( $(date +%s) - $(stat -f %m "$log") ))
    if [[ "$log_age" -le "$LA_SWAP_HEALTHY_WINDOW" && "$force" != "force" ]]; then
        echo "remote-session: --swap-model: :$port was active ${log_age}s ago (<${LA_SWAP_HEALTHY_WINDOW}s window) — the session looks ALIVE; refusing. --force overrides." >&2
        return 2
    fi
    entry="$(_entry_for "$alias")"
    [[ -n "$entry" ]] || { echo "remote-session: --swap-model: unknown remote alias: $alias" >&2; return 2; }
    prov="$(_field "$entry" 2)"; model="$(_field "$entry" 3)"
    _available "$prov" || { echo "remote-session: --swap-model: no key saved for provider $prov" >&2; return 2; }
    [[ "$model" != "SELECT" ]] || {
        echo "remote-session: --swap-model: '$alias' has no fixed model (catalog-only); pick a concrete alias." >&2
        return 2; }
    # Faithful to the SESSION's own settings: thinking from the alias suffix, effort and
    # temperature parsed from the config the session actually runs with.
    thinking="false"; [[ "$alias" == *-thinking ]] && thinking="true"
    effort="$(python3 -c 'import re,sys; t=open(sys.argv[1]).read(); m=re.search(r"^\s+reasoning_effort: (\S+)", t, re.M); print(m.group(1) if m else "")' "$cfg")"
    temperature="$(python3 -c 'import re,sys; t=open(sys.argv[1]).read(); m=re.search(r"^\s+temperature: (\S+)", t, re.M); print(m.group(1) if m else "")' "$cfg")"

    # --- PRE-FLIGHT: prove the new model on a scratch port; nothing live is touched -------
    # The scratch proxy runs with the hook limiter OFF: it isolates "does this model serve?"
    # from the shared 40 RPM queue. The live proxy keeps the limiter.
    local scratch scratch_cfg scratch_log scratch_pid scratch_pidf probe
    scratch="$(swap_scratch_port)" || return 1
    scratch_cfg="$RUNDIR/swap-scratch-$port.yaml"; scratch_log="$RUNDIR/swap-scratch-$port.log"
    scratch_pidf="$RUNDIR/swap-scratch-$port.pid"
    _decide_native_route "$prov" "$model"
    write_proxy_config "$scratch_cfg" "$prov" "$model" "$thinking" "$effort" "$temperature" || return 2
    scratch_pid="$(LA_PROXY_HOOKS_RATE_LIMIT=0 _spawn_proxy_on "$scratch" "$scratch_cfg" "$scratch_log" "$prov")" || return 2
    echo "$scratch_pid" > "$scratch_pidf"
    if ! _wait_proxy_ready "$scratch" "$scratch_pid" 90; then
        echo "remote-session: --swap-model: pre-flight proxy did not become ready -> ABORT, nothing changed." >&2
        kill "$scratch_pid" 2>/dev/null; rm -f "$scratch_pidf" "$scratch_cfg" "$scratch_log"
        return 1
    fi
    probe="$(_swap_probe "$scratch" "$scratch_cfg" 300)"
    echo "remote-session: --swap-model: pre-flight probe $probe"
    kill "$scratch_pid" 2>/dev/null; rm -f "$scratch_pidf" "$scratch_cfg" "$scratch_log"
    [[ "$probe" == 200* ]] || {
        echo "remote-session: --swap-model: pre-flight FAILED -> ABORT, nothing changed." >&2
        return 1; }

    # --- THE SWAP: backup, rewrite the SAME path (teardown keys on it), relaunch ------------
    local stamp bak newpid probe2
    stamp="$(date +%Y%m%d-%H%M%S)"
    bak="${cfg}.bak-${stamp}"
    cp -p "$cfg" "$bak"                      # keeps mode 0600; the paper trail stays on disk
    write_proxy_config "$cfg" "$prov" "$model" "$thinking" "$effort" "$temperature" || {
        cp -p "$bak" "$cfg"; return 1; }
    kill "$pid" 2>/dev/null
    for _ in $(seq 1 30); do                 # let the port actually free before rebinding
        lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || break
        sleep 1
    done
    newpid="$(_spawn_proxy_on "$port" "$cfg" "$log" "$prov")" || {
        _swap_revert "$port" "$bak" "$cfg" "$log" "$pidf" "" "$prov"; return 1; }
    # The .owner file is NOT touched: the session keeps its launcher identity, so --stop and
    # stale-cleanup still recognize this proxy as theirs.
    echo "$newpid" > "$pidf"
    if ! _wait_proxy_ready "$port" "$newpid" 300; then
        _swap_revert "$port" "$bak" "$cfg" "$log" "$pidf" "$newpid" "$prov"
        return 1
    fi
    probe2="$(_swap_probe "$port" "$cfg" "$LA_SWAP_PROBE_TIMEOUT")"
    echo "remote-session: --swap-model: live probe $probe2"
    if [[ "$probe2" != 200* ]]; then
        _swap_revert "$port" "$bak" "$cfg" "$log" "$pidf" "$newpid" "$prov"
        return 1
    fi
    local tty=""
    if [[ -r "$ownerf" ]]; then
        tty="$(ps -o tty= -p "$(awk '{print $1}' "$ownerf")" 2>/dev/null | tr -d ' ')"
    fi
    echo "remote-session: swapped :$port to $model ($alias). The session there is idle: press Enter (or send any message) in it to resume."
    [[ -n "$tty" && "$tty" != "?" && "$tty" != "??" ]] && \
        echo "  (its terminal: $tty)"
    return 0
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
STOP_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)        usage; exit 0 ;;
        --print-spoof)    printf '%s\n' "$(_select_spoof_primary)"; exit 0 ;;
        --list)           MODE="list"; shift ;;
        --stop)           MODE="stop"; shift
                          while [[ $# -gt 0 && ( "$1" == --force || "$1" =~ ^[0-9]+$ ) ]]; do
                              STOP_ARGS+=("$1"); shift
                          done ;;
        --swap-model)     MODE="swap-model"; SWAP_PORT="${2:-}"; SWAP_ALIAS="${3:-}"; shift
                          [[ -n "$SWAP_PORT" && -n "$SWAP_ALIAS" ]] || {
                              echo 'remote-session: --swap-model needs <port> <alias>' >&2; exit 2; }
                          shift 2 ;;
        --force)          SWAP_FORCE=1; shift ;;
        --verify)         MODE="verify"; ALIAS="${2:-}"; shift 2 || shift ;;
        --models)         MODE="models"; ALIAS="${2:-}"; shift 2 || shift ;;
        --remote-model)   [[ $# -ge 2 ]] || { echo 'remote-session: --remote-model needs an ID' >&2; exit 2; }
                          REMOTE_MODEL="$2"; _valid_model "$REMOTE_MODEL" || exit 2; shift 2 ;;
        --dry-run)        DRY_RUN=1; shift ;;
        --include-trials) INCLUDE_TRIALS=1; shift ;;
        --local-capable-shown) LOCAL_CAPABLE_SHOWN=1; shift ;;
        --show-paid-only) SHOW_PAID_ONLY_MODELS=1; shift ;;
        --effort)         [[ $# -ge 2 ]] || { echo 'remote-session: --effort needs a level' >&2; exit 2; }
                          case "$2" in low|medium|high|xhigh|max) EFFORT_CHOICE="$2" ;;
                              *) echo "remote-session: --effort must be low|medium|high|xhigh|max (omit it for the provider default)" >&2; exit 2 ;;
                          esac; shift 2 ;;
        --inventory)      MODE="inventory"; shift ;;
        --csl-owner)
            CSL_OWNER=1; shift ;;
        --csl-nav-file)
            [[ $# -ge 2 ]] || { echo 'remote-session: --csl-nav-file needs a path' >&2; exit 2; }
            export CSL_NAV_FILE="$2"; shift 2 ;;
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
        --temperature)    [[ $# -ge 2 ]] || { echo 'remote-session: --temperature needs a value (0.0-2.0)' >&2; exit 2; }
                          TEMPERATURE_CHOICE="$2"; shift 2 ;;
        --show-broken)    SHOW_BROKEN_MODELS=1; shift ;;
        --show-old)       SHOW_OLD_MODELS=1; shift ;;
        --enable-mcp)     export LA_REMOTE_ENABLE_MCP=1; shift ;;
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
    inventory) print_inventory; exit 0 ;;
    stop) stop_proxy "${STOP_ARGS[@]+"${STOP_ARGS[@]}"}"; exit 0 ;;
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
# No alias: open the shared session picker at the Remote screen as a DIRECT ROOT (Quit, no
# Back). The picker launches the chosen alias by calling this script again WITH an alias, as
# a child, and returns to the picker when that session ends — so every direct-alias path
# below (proxy, cleanup trap, trial guard) is the one and only launch path.
# Special MODEs carry no alias BY DESIGN (2026-10-24, Phase-4 dogfood finding: --swap-model
# and --install-keys died here at the picker exec before their handlers ever ran — the swap
# was unreachable from the CLI, and headless it surfaced as a TTY error).
if [[ -z "$ALIAS" && "$MODE" == "launch" ]]; then
    # If running under csl ownership (--csl-owner), use the internal menu loop instead of
    # delegating to session-picker, so we can return navigation state via CSL_NAV_FILE.
    # For csl_owner, we don't require a tty since csl manages the interaction.
    if [[ "${CSL_OWNER:-0}" -eq 1 ]]; then
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
                    _is_broken_hidden "$(_field "$e" 1)" && continue
                    choices+=("$(_field "$e" 1)")
                done

                current_choices=("${choices[@]}")

                # Render the menu box to stderr.
                _box top
                _box row "  Remote cloud-API agents  (${#choices[@]} available)"
                _box mid
                local idx=1
                for alias in "${choices[@]}"; do
                    local e entry prov model disp tier
                    entry="$(_entry_for "$alias")" || continue
                    prov="$(_field "$entry" 2)"; model="$(_field "$entry" 3)"
                    disp="$(_field "$entry" 4)"; tier="$(_field "$entry" 5)"
                    local keystate
                    if "$KEYS" --check "$prov" >/dev/null 2>&1; then keystate="✓ $prov"; else keystate="✗ $prov (no key)"; fi
                    _box row "  $idx) $disp  [$(_tier_label "$tier" "$prov")]  $keystate"
                    idx=$((idx+1))
                done
                _box mid
                _box row "  Actions:"
                _box row "  h) Home / csl picker      s) Local picker"
                _box row "  f) Toggle local-capable   R) Hidden-model report"
                _box row "  k) Install/set up keys    a) Auto-mode: $(case "$AUTO_MODE_STATE" in 0) echo "blind-trust" ;; 1) echo "classifier" ;; 2) echo "off" ;; esac)"
                _box row "  t) Telemetry: $( [[ "$TELEMETRY_ENABLED" -eq 1 ]] && echo "ON" || echo "OFF" )    l) Local-capable: $( [[ "$LOCAL_CAPABLE_SHOWN" -eq 1 ]] && echo "SHOWN" || echo "HIDDEN" )"
                _box row "  m) MCPs: $( [[ "${LA_REMOTE_ENABLE_MCP:-0}" -eq 1 ]] && echo "ENABLED" || echo "DISABLED" )    u) $EMOJI_BROKEN_MODELS Unworking: $( [[ "${SHOW_BROKEN_MODELS:-0}" -eq 1 ]] && echo "SHOWN" || echo "HIDDEN" )"
                _box row "  p) Paid-only models: $( [[ "$SHOW_PAID_ONLY_MODELS" -eq 1 ]] && echo "SHOWN" || echo "HIDDEN" )"
                _box row "  q) Quit"
                _box bottom
                printf "Select [1-%d] (h/e/s/f/R/k/a/t/l/m/u/p/q): " "${#choices[@]}" >&2
                read -r -p "" sel >&2 || { _nav "quit"; return 0; }
                case "$sel" in
                    h|H) _nav "home"; return 0 ;;
                    s|S) _nav "local"; return 0 ;;
                    f|F)
                        LOCAL_CAPABLE_SHOWN=$(( 1 - LOCAL_CAPABLE_SHOWN ))
                        _lc_load_policy
                        continue ;;
                    R|r)
                        _lc_load_policy
                        echo >&2
                        echo "  Hidden local-capable models (grouped by provider):" >&2
                        (
                            for e in "${LA_REMOTE_AGENTS[@]}"; do
                                alias="$(_field "$e" 1)"; prov="$(_field "$e" 2)"; model="$(_field "$e" 3)"
                                disp="$(_field "$e" 4)"; tier="$(_field "$e" 5)"
                                if _lc_is_hidden "$prov" "$model"; then
                                    printf '%s\n' "$alias" >&2
                                fi
                            done | sort
                        ) || echo "  (report unavailable)" >&2
                        echo >&2
                        echo "  (press enter to return to menu)" >&2
                        read -r -p "" _ >&2 || { _nav "quit"; return 0; }
                        continue ;;
                    k|K) python3 "$REPO_ROOT/install/setup-api-keys.py"; continue ;;
                    a|A) AUTO_MODE_STATE=$(( (AUTO_MODE_STATE + 1) % 3 )); continue ;;
                    t|T)
                        if [[ "$sel" == "t" ]]; then
                            TELEMETRY_ENABLED=$(( TELEMETRY_ENABLED ^ 1 ))
                        fi
                        continue ;;
                    l|L)
                        LOCAL_CAPABLE_SHOWN=$(( 1 - LOCAL_CAPABLE_SHOWN ))
                        _lc_load_policy
                        continue ;;
                    m|M)
                        if [[ "${LA_REMOTE_ENABLE_MCP:-0}" -eq 1 ]]; then
                            export LA_REMOTE_ENABLE_MCP=0
                        else
                            export LA_REMOTE_ENABLE_MCP=1
                        fi
                        continue ;;
                    # u only: b is Back in the picker and letters are case-insensitive there.
                    u|U)
                        SHOW_BROKEN_MODELS=$(( 1 - ${SHOW_BROKEN_MODELS:-0} ))
                        continue ;;
                    p|P)
                        SHOW_PAID_ONLY_MODELS=$(( 1 - SHOW_PAID_ONLY_MODELS ))
                        continue ;;
                    c|C)
                        # Choose effort
                        local efforts=("low" "medium" "high" "xhigh" "max")
                        local i=1
                        for eff in "${efforts[@]}"; do printf "  %d) %s\n" "$i" "$eff" >&2; i=$((i+1)); done
                        local c; printf "  Choose effort [1-5] (current: ${EFFORT_CHOICE:-<provider default>}): " >&2; read -r c
                        if [[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -ge 1 ] && [ "$c" -le 5 ]; then
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
                        # Write state to nav file before returning so caller (which may be in a subshell)
                        # can read updated toggles. We don't call _nav because that writes a navigation
                        # target; we just want to sync state.
                        _sync_state
                        ;;
                esac
            done
            } >&2
            # Return the selected alias via stdout (compatible with existing callers).
            printf '%s' "$selection"
        }

        # _run_remote_menu returns 0 (not an error) on navigation (h/l/q) — it wrote a
        # nav token via _nav() and printed nothing on stdout. `ALIAS="$(...)" || ...` only
        # catches a NON-ZERO exit, so an empty-but-successful return used to fall straight
        # through into alias resolution below with ALIAS="", producing a bogus
        # "unknown remote alias: " error instead of a clean exit. Check emptiness explicitly.
        # Also read state from nav file because command substitution runs in a subshell
        # and loses variable assignments. The nav file is keyed on this shell's $$.
        navfile="${CSL_NAV_FILE:-/tmp/_csl_nav.$$}"
        rm -f "$navfile"
        ALIAS="$(_run_remote_menu)" || { echo "remote-session: nothing selected." >&2; exit 1; }
        # Read synced state from nav file (written by _sync_state on selection, or _nav on navigate)
        # First line is navigation target (home/local/quit/stay), subsequent lines are KEY=VALUE state
        if [[ -f "$navfile" ]]; then
            _nav_target=""
            while IFS= read -r line; do
                if [[ -z "$_nav_target" ]]; then
                    _nav_target="$line"
                else
                    case "$line" in
                        AUTO_MODE_STATE=*) AUTO_MODE_STATE="${line#*=}" ;;
                        LOCAL_CAPABLE=*)   LOCAL_CAPABLE_SHOWN="${line#*=}" ;;
                        TELEMETRY=*)       TELEMETRY_ENABLED="${line#*=}" ;;
                        INCLUDE_TRIALS=*)  INCLUDE_TRIALS="${line#*=}" ;;
                        EFFORT_CHOICE=*)   EFFORT_CHOICE="${line#*=}" ;;
                        SHOW_PAID_ONLY_MODELS=*) SHOW_PAID_ONLY_MODELS="${line#*=}" ;;
                    esac
                fi
            done < "$navfile"
            # Handle navigation targets
            case "$_nav_target" in
                home|local|quit) exit 0 ;;  # navigated away
                stay) ;;  # stay in remote lane with updated state
                "") exit 0 ;;  # empty nav file = navigated away
            esac
        fi
        if [[ -z "$ALIAS" ]]; then
            # Navigated away (home/local) or quit — csl reads the nav token itself when
            # CSL_OWNER=1; a direct invocation with nothing selected just exits cleanly.
            exit 0
        fi
    else
        _picker_args=(remote)
        [[ $INCLUDE_TRIALS -eq 1 ]] && _picker_args+=(--include-trials)
        [[ $LOCAL_CAPABLE_SHOWN -eq 1 ]] && _picker_args+=(--local-capable-shown)
        [[ $SHOW_OLD_MODELS -eq 1 ]] && _picker_args+=(--show-old)
        trap - EXIT
        exec "$SCRIPT_DIR/session-picker" "${_picker_args[@]}"
    fi
fi

# ---- interactive toggle resolution (launch mode) ---------------------------
# These map the parsed toggles onto what `claude` and the environment actually read.
# NOTE the history here: an earlier version of this file mapped the toggles inside a
# `_launch()` helper that was NEVER CALLED, so `-a` and `-t` silently did nothing while
# the picker advertised them. Anything that resolves a toggle must therefore sit on the
# live path (the exec near the bottom of this file), not in a helper.
# LA_REMOTE_CLAUDE_SETTINGS (0.26.5; claimed in 0.14.8): a user settings file for free-API sessions.
# Resolved BEFORE any launch or dry-run, so a missing file is reported in both.
REMOTE_CLAUDE_SETTINGS_FILE=""
if [[ -n "${LA_REMOTE_CLAUDE_SETTINGS:-}" ]]; then
    if [[ -f "$LA_REMOTE_CLAUDE_SETTINGS" ]]; then
        REMOTE_CLAUDE_SETTINGS_FILE="$LA_REMOTE_CLAUDE_SETTINGS"
    else
        echo "remote-session: warning: LA_REMOTE_CLAUDE_SETTINGS set but file not found: $LA_REMOTE_CLAUDE_SETTINGS" >&2
    fi
fi

if [[ "$MODE" == "launch" ]]; then
    # Auto-mode state -> the flag `claude` actually reads.
    #   0 = blind-trust  -> --permission-mode auto   (DEFAULT; no classifier in the loop)
    #   1 = classifier   -> --permission-mode auto   (+ classifier, see below)
    #   2 = off          -> --permission-mode acceptEdits
    # Unlike a LOCAL session, remote-session.sh execs `claude` directly rather than going
    # through launch-claude-agent.sh, so LA_AUTO_MODE/LA_BLIND_AUTO are NOT read by anyone
    # here. We must pass --permission-mode ourselves; exporting those vars alone was the
    # original bug. They are still exported for any child that inspects them.
    # AUTO_MODE_STATE=0 (blind-trust): use bypassPermissions to BYPASS cloud classifier (measured: 0 classifier calls).
    # AUTO_MODE_STATE=1 (classifier): GENUINE auto mode (0.28.0). Claude Code's classifier asks for
    #   claude-sonnet-5[1m]; the proxy routes it (write_proxy_config: LA_CLASSIFIER_MODEL, else the
    #   "claude-*" catch-all = the session model) to a free model. Measured 2026-10-09 on NVIDIA
    #   Nemotron 3 Ultra: stage-1 ok in 11.8s, stage-2 ok in 84.9s, `curl … | sh` BLOCKED
    #   ([Containment Escape]), a benign echo allowed. Fail-closed: an upstream error is a refusal.
    # AUTO_MODE_STATE=2 (off): acceptEdits.
    case "$AUTO_MODE_STATE" in
        0) PERMISSION_MODE="bypassPermissions"; LA_AUTO_MODE=1; LA_BLIND_AUTO=1 ;;
        1) PERMISSION_MODE="auto"; LA_AUTO_MODE=1; LA_BLIND_AUTO=0 ;;
        2) PERMISSION_MODE="acceptEdits"; LA_AUTO_MODE=0; LA_BLIND_AUTO=0 ;;
    esac
    export LA_AUTO_MODE LA_BLIND_AUTO

    # Cloud bypass permissions (blind-trust for cloud sessions) - can be set independently
    # of auto-mode state. When ON, uses bypassPermissions regardless of AUTO_MODE_STATE.
    : "${CLOUD_BYPASS_PERMISSIONS:=0}"
    if [[ "$CLOUD_BYPASS_PERMISSIONS" -eq 1 ]]; then
        PERMISSION_MODE="bypassPermissions"
        LA_AUTO_MODE=1
        LA_BLIND_AUTO=1
    fi
    export LA_AUTO_MODE LA_BLIND_AUTO

    # Classifier source for Auto Mode (when not blind-trust).
    #   0 = NVIDIA API (fallback: Devstral local)
    #   1 = Local Devstral (always)
    #   2 = Auto: local classifier on local session, NVIDIA API on remote session
    # Default is 2 (Auto).
    : "${LA_CLASSIFIER_SOURCE:=2}"
    export LA_CLASSIFIER_SOURCE

    # Where the classifier runs. Source 1 (local Devstral) needs a local server registered under
    # the classifier's name; a remote session has none, so say so and use the remote model instead.
    if [[ "$AUTO_MODE_STATE" -eq 1 ]]; then
        if [[ "$LA_CLASSIFIER_SOURCE" == 1 ]]; then
            echo "$EMOJI_WARNING  auto-mode: classifier source 'local Devstral' is not reachable from a free-API"
            echo "    session (no local classifier server on this lane) — using the remote classifier route."
        fi
        echo "   auto-mode: classifier → ${LA_CLASSIFIER_MODEL:-the session model} (fail-closed; an upstream error refuses the action)"
    fi

    # Blind-trust auto mode (AUTO_MODE_STATE=0): generate settings via the single source of truth.
    if [[ "$AUTO_MODE_STATE" -eq 0 ]]; then
        BLIND_TRUST_SETTINGS_FILE="${TMPDIR:-/tmp}/claude-blind-trust-settings-remote-$$.json"
        # Resolve allowlist files: user file in ~/.claude/launch-profiles/ if exists, else shipped example
        MASTER_ALLOWLIST_FILE="$HOME/.claude/launch-profiles/allowlist-master.json"
        if [[ ! -f "$MASTER_ALLOWLIST_FILE" ]]; then
            MASTER_ALLOWLIST_FILE="$SCRIPT_DIR/../config/launch-profiles/allowlist-master.example.json"
            echo "remote-session: using example allowlist-master (copy to ~/.claude/launch-profiles/ to customize)" >&2
        fi
        PROFILE_ALLOWLIST_FILE="$HOME/.claude/launch-profiles/lean-cloud-general.json"
        if [[ ! -f "$PROFILE_ALLOWLIST_FILE" ]]; then
            PROFILE_ALLOWLIST_FILE="$SCRIPT_DIR/../config/launch-profiles/lean-cloud-general.example.json"
            echo "remote-session: using example lean-cloud-general (copy to ~/.claude/launch-profiles/ to customize)" >&2
        fi
        MCP_FLAG=()
        [[ "${LA_REMOTE_ENABLE_MCP:-0}" -eq 1 ]] && MCP_FLAG=(--enable-mcp)
        EXTRA_BT_FLAGS=()
        [[ "${LA_DENY_GH_RELEASE:-0}" -eq 1 ]] && EXTRA_BT_FLAGS+=(--deny-gh-release)
        python3 "$SCRIPT_DIR/blind-trust-settings.py" \
            --master "$MASTER_ALLOWLIST_FILE" \
            --profile "$PROFILE_ALLOWLIST_FILE" \
            --out "$BLIND_TRUST_SETTINGS_FILE" \
            "${MCP_FLAG[@]}" \
            "${EXTRA_BT_FLAGS[@]}" \
            --mechanism bypass || {
            echo "remote-session: failed to generate blind-trust settings" >&2; exit 1; }
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
if [[ "$MODE" == "swap-model" ]]; then
    swap_model "$SWAP_PORT" "$SWAP_ALIAS" "${SWAP_FORCE:+force}"
    exit $?
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

# Cerebras: its free tier caps output at 40960 and reserves tokens from the per-minute budget
# based on the requested max_tokens. To avoid burning 30k/min on a single 128000 request that
# gets silently capped, lower the ceiling for Cerebras unless the user explicitly overrode it.
if [[ "$PROV" == "cerebras" && -z "${LA_REMOTE_MAX_OUTPUT_TOKENS:-}" ]]; then
    if (( MAX_OUT > 40960 )); then
        MAX_OUT=40960
    fi
fi

DISP="$(_field "$ENTRY" 4)"; TIER="$(_field "$ENTRY" 5)"
# Extract autocompaction default from roster (7th field) — used when LA_AUTO_COMPACT_WINDOW not set
AC_DEFAULT="$(_field "$ENTRY" 7)"
if [[ -z "${LA_AUTO_COMPACT_WINDOW:-}" && -n "$AC_DEFAULT" && "$AC_DEFAULT" != "-" ]]; then
    LA_AUTO_COMPACT_WINDOW="$AC_DEFAULT"
fi

# LA_AUTO_COMPACT_WINDOW validation — mirrors launch-claude-agent.sh (must be before DRY_RUN exit)
: "${LA_AUTO_COMPACT_WINDOW:=}"
if [ -n "$LA_AUTO_COMPACT_WINDOW" ]; then
    # Validate using the same python validator as local launchers
    if ! command -v python3 >/dev/null 2>&1; then
        echo "remote-session: python3 is required to validate LA_AUTO_COMPACT_WINDOW." >&2
        exit 1
    fi
    if ! python3 -c '
import re
import sys

value = sys.argv[1].strip().lower()
if value == "auto":
    raise SystemExit(0)

match = re.fullmatch(r"([0-9]+)([km]?)", value)
if not match:
    raise SystemExit(1)

amount = int(match.group(1))
suffix = match.group(2)
multiplier = {"": 1, "k": 1000, "m": 1000000}[suffix]
tokens = amount * multiplier
raise SystemExit(0 if 100000 <= tokens <= 1000000 else 1)
' "$LA_AUTO_COMPACT_WINDOW"
    then
        echo "remote-session: LA_AUTO_COMPACT_WINDOW must be auto or 100k–1m tokens." >&2
        exit 1
    fi
fi

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
SPOOF_PRIMARY="$(_select_spoof_primary)"
if [[ $DRY_RUN -eq 1 ]]; then
    echo "   DRY RUN  : would start a LiteLLM proxy and exec claude against it."
    echo "              nothing started, no network call made."
    echo "     model          : $MODEL"
    echo "     spoof          : $SPOOF_PRIMARY"
    echo "     provider       : $PROV"
    echo "     cost note      : $COST_NOTE"
    # Print the RESOLVED toggle state and the flags claude would actually receive.
    # A dry run that stops before this point is how the dead-switch bugs stayed hidden:
    # the toggles parsed, printed nothing, and were never applied. Showing the resolved
    # values makes each switch verifiable by OUTCOME without launching anything.
    case "$AUTO_MODE_STATE" in
        0) _am_label="blind-trust (bypassPermissions + DESTRUCTIVE_DENY, no classifier)" ;;
        1) _am_label="classifier (--permission-mode auto; claude-sonnet-5 → ${LA_CLASSIFIER_MODEL:-session model})" ;;
        2) _am_label="off (acceptEdits)" ;;
    esac
    # MCP status
    if [[ "${LA_REMOTE_ENABLE_MCP:-0}" -eq 1 && "$AUTO_MODE_STATE" -ne 0 ]]; then
        _mcp_status="ENABLED (free-API session, auto-mode off/classifier)"
    elif [[ "${LA_REMOTE_ENABLE_MCP:-0}" -eq 1 && "$AUTO_MODE_STATE" -eq 0 ]]; then
        _mcp_status="ENABLED (blind-trust mode — mcp__* allowlisted in generated settings)"
    elif [[ "$AUTO_MODE_STATE" -eq 0 ]]; then
        _mcp_status="DISABLED (blind-trust auto-mode; set LA_REMOTE_ENABLE_MCP=1 to enable)"
    else
        _mcp_status="DISABLED (default; set LA_REMOTE_ENABLE_MCP=1 to enable)"
    fi
    echo
    echo "   resolved toggles:"
    echo "     auto-mode      : $_am_label"
    echo "     permission-mode: --permission-mode $PERMISSION_MODE"
    echo "     mcps           : $_mcp_status"
    if [[ "$AUTO_MODE_STATE" -eq 0 && -n "$BLIND_TRUST_SETTINGS_FILE" ]]; then
        echo "     settings       : --settings $BLIND_TRUST_SETTINGS_FILE (generated blind-trust via blind-trust-settings.py, bypassPermissions + DESTRUCTIVE_DENY)"
    fi
    if [[ "${LA_REMOTE_TELEMETRY:-0}" -eq 1 ]]; then
        echo "     telemetry      : ON  (stock Claude Code reporting)"
    else
        echo "     telemetry      : OFF (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1)"
    fi
    echo "     effort         : ${EFFORT_CHOICE:-<provider default>}"
    [[ -n "${LA_AUTO_COMPACT_WINDOW:-}" ]] && echo "     autocompact    : --autocompact $LA_AUTO_COMPACT_WINDOW"
    echo
    exit 0
fi

_prepare_runtime_dir || exit 1
echo "   proxy    : starting LiteLLM (Anthropic /v1/messages → $PROV)…"
PORT="$(start_proxy "$PROV" "$MODEL" "$THINKING" "$EFFORT_CHOICE" "$TEMPERATURE_CHOICE")" || {
    echo "remote-session: could not start the translating proxy." >&2; exit 1; }
echo "   proxy    : ready on http://127.0.0.1:$PORT"
# start_proxy ran in a subshell, so read the lane it chose back from the config it wrote.
if grep -q 'supported_endpoints: \["/v1/messages"\]' "$RUNDIR/proxy-$PORT.yaml" 2>/dev/null; then
    echo "   lane     : native Anthropic /v1/messages (effort and thinking sent as Claude Code wrote them)"
else
    echo "   lane     : chat/completions translation"
fi
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
# 30 minutes (1800000ms), matching the LOCAL lanes' precedent (launch-claude-agent.sh): a slow
# free-API model (DeepSeek V4.1 Flash answers in ~10 min) must not have its session killed
# mid-turn by the stock 300000ms cap — a timeout that stalls an unattended session until the
# user returns is the worst outcome (user 2026-10-22, after exactly that hit an EchoPrism
# overnight run). Overridable via LA_REMOTE_API_TIMEOUT_MS.
export API_TIMEOUT_MS="${LA_REMOTE_API_TIMEOUT_MS:-1800000}"
export API_FORCE_IDLE_TIMEOUT=0
export CLAUDE_ENABLE_STREAM_WATCHDOG=0
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1    # no telemetry through a third party
export CLAUDE_IS_REMOTE_API="true"                   # distinct from CLAUDE_IS_LOCAL
export LA_SESSION_LAUNCHER="remote-session.sh"       # names the launcher for plugin hooks (stop-hook gate)
export LA_QUEUE_STOP_HOOK="${LA_QUEUE_STOP_HOOK:-0}" # queued-prompt Stop hook; OFF unless turned on (0.22.3)
export LA_REMOTE_AGENT="$ALIAS"
export LA_REMOTE_PROVIDER="$PROV"

# Provide resolver inputs so it can emit truthful identity for remote sessions
export MODEL_ALIAS="$ALIAS"                          # the remote agent alias (e.g. nvidia-nemotron-3-ultra)
# The session title names the MODEL as a person reads it, never the provider-prefixed alias
# (user 2026-10-22): the roster's curated name when the launched model IS the roster's, else
# the readable form of the id that was picked from a catalog or passed with --remote-model.
if [[ "$MODEL" == "$(_field "$ENTRY" 3)" ]]; then
    LA_SESSION_TITLE_MODEL="$DISP"
else
    LA_SESSION_TITLE_MODEL="$(python3 "$SCRIPT_DIR/model_names.py" "$MODEL" 2>/dev/null || printf '%s' "$MODEL")"
fi
export LA_SESSION_TITLE_MODEL
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

# Also write to per-session file for statusline/resolver to read (since they run in a different process tree)
# Use fixed /tmp to avoid TMPDIR differences between launcher and resolver processes
LA_EFFORT_FILE="/tmp/claude-effort-${LA_SESSION_ID}"
printf '%s' "${EFFORT_CHOICE:-medium}" > "$LA_EFFORT_FILE" 2>/dev/null || true

# SESSION IDENTITY RESOLUTION — emit deterministic identity for consumers
# (statusline, transcript marker, hooks). Must run AFTER endpoint is known.
if [ -x "$SCRIPT_DIR/la-session-identity.sh" ]; then
    SESSION_IDENTITY=$("$SCRIPT_DIR/la-session-identity.sh" 2>/dev/null || true)
    if [ -n "$SESSION_IDENTITY" ]; then
        export LA_SESSION_IDENTITY="$SESSION_IDENTITY"
        # Export individual fields for easy consumption by hooks/statusline
        LA_SESSION_KIND=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"session_kind":"[^"]*"' | cut -d'"' -f4); export LA_SESSION_KIND
        LA_ACTUAL_MODEL=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"actual_model_id":"[^"]*"' | cut -d'"' -f4); export LA_ACTUAL_MODEL
        LA_PROVIDER_DISPLAY=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"provider_display":"[^"]*"' | cut -d'"' -f4); export LA_PROVIDER_DISPLAY
        LA_ACTUAL_MODEL_DISPLAY=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"actual_model_display":"[^"]*"' | cut -d'"' -f4); export LA_ACTUAL_MODEL_DISPLAY
        LA_BANNER_MODEL_LABEL=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"actual_model_display":"[^"]*"' | cut -d'"' -f4); export LA_BANNER_MODEL_LABEL
        LA_THEME_IDENTIFIER=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"theme_identifier":"[^"]*"' | cut -d'"' -f4); export LA_THEME_IDENTIFIER
        LA_SPINNER_PROFILE=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"spinner_profile_id":"[^"]*"' | cut -d'"' -f4); export LA_SPINNER_PROFILE
        LA_TRANSCRIPT_MARKER_VERSION=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"transcript_marker_version":[0-9]*' | cut -d':' -f2); export LA_TRANSCRIPT_MARKER_VERSION
        LA_SESSION_KIND_EMOJI=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"session_emoji":"[^"]*"' | cut -d'"' -f4); export LA_SESSION_KIND_EMOJI
        LA_SESSION_ID=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"session_id":"[^"]*"' | cut -d'"' -f4); export LA_SESSION_ID
    fi
fi

# SESSION-IDENTITY MOD — model-weighted spinner words (mods/session-identity); see the same
# block in launch-claude-agent.sh. Before the settings generator, which reads LA_MOD_ATTACHED.
LA_MOD_DIR="$("$SCRIPT_DIR/la-mod-attach.sh" 2>/dev/null || true)"
if [ -n "$LA_MOD_DIR" ]; then
    LA_MODEL_SPINNER_WORDS="$(LAUNCH_DIR="$SCRIPT_DIR" "$SCRIPT_DIR/generate-remote-settings.py" \
        --emit-model-words --identity-json "${LA_SESSION_IDENTITY:-{\}}" 2>/dev/null || echo '[]')"
    export LA_MODEL_SPINNER_WORDS
    LA_MOD_ATTACHED=1; export LA_MOD_ATTACHED
fi

# PER-SESSION SETTINGS — generate theme + spinner overlay for remote sessions only.
# This creates a transient settings file passed via --settings, NOT written to user's
# persistent ~/.claude/settings.json. Only applied for free_api sessions (session_kind=free_api).
if [ "${LA_SESSION_KIND:-}" = "free_api" ] && [ -x "$SCRIPT_DIR/generate-remote-settings.py" ]; then
    SETTINGS_FILE="$(
        la_mktemp_json "${TMPDIR:-/tmp}/free-agents-settings"
    )"
    chmod 600 "$SETTINGS_FILE"
    LA_SPOOF_MODEL_ID="$SPOOF_PRIMARY" LAUNCH_DIR="$SCRIPT_DIR" "$SCRIPT_DIR/generate-remote-settings.py" \
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
    BLIND_TRUST_BANNER="   blind-trust: bypassPermissions + DESTRUCTIVE_DENY (no classifier, measured 0 calls)"
fi
# Add autocompaction to banner
AUTOCOMPACT_BANNER=""
[[ -n "${LA_AUTO_COMPACT_WINDOW:-}" ]] && AUTOCOMPACT_BANNER="   autocompact : $LA_AUTO_COMPACT_WINDOW"

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
$AUTOCOMPACT_BANNER
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
AGENT_PROMPT=${AGENT_PROMPT//__LA_REMOTE_SPOOF__/$SPOOF_PRIMARY}

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

# By default, remote free-API sessions disable MCPs (--mcp-config '{"mcpServers":{}}')
# to save cost on paid gateway. For free API sessions, allow enabling MCPs via
# LA_REMOTE_ENABLE_MCP=1. Blind-trust mode (AUTO_MODE_STATE=0) still disables them
# by default since the sandbox allowlist doesn't include MCP tools.
if [[ "${LA_REMOTE_ENABLE_MCP:-0}" -eq 1 && "${AUTO_MODE_STATE:-0}" -ne 0 ]]; then
    # MCPs enabled for free-API non-blind-trust sessions
    claude_cmd=(claude --model "$SPOOF_PRIMARY" --append-system-prompt "$AGENT_PROMPT")
else
    # Default: disable MCPs (original behavior for cost savings)
    claude_cmd=(claude --model "$SPOOF_PRIMARY" --strict-mcp-config --mcp-config '{"mcpServers":{}}' --append-system-prompt "$AGENT_PROMPT")
fi
claude_cmd+=(--permission-mode "$PERMISSION_MODE")
[ -n "${LA_MOD_DIR:-}" ] && claude_cmd+=(--plugin-dir "$LA_MOD_DIR")

# Handle settings merge: if both blind-trust and per-session settings exist, merge them.
# Claude Code only accepts ONE --settings file (last wins), so we must merge.
# LA_REMOTE_CLAUDE_SETTINGS: if set and file exists, use as per-session settings file
# (merged like SETTINGS_FILE via merge-settings.py if blind-trust is also active).
# If set but missing, print warning and continue without it.

FINAL_SETTINGS_FILE=""
# Three-way merge priority: blind-trust (base) -> LA_REMOTE_CLAUDE_SETTINGS -> generated SETTINGS_FILE
if [[ "$AUTO_MODE_STATE" -eq 0 && -n "$BLIND_TRUST_SETTINGS_FILE" && -f "$BLIND_TRUST_SETTINGS_FILE" ]]; then
    # Start with blind-trust as base
    _base_settings="$BLIND_TRUST_SETTINGS_FILE"
    _overlay_settings=""

    # Add LA_REMOTE_CLAUDE_SETTINGS as first overlay if present
    if [[ -n "$REMOTE_CLAUDE_SETTINGS_FILE" ]]; then
        _overlay_settings="$REMOTE_CLAUDE_SETTINGS_FILE"
    fi

    # Add generated SETTINGS_FILE as second overlay if present
    if [ "${LA_SESSION_KIND:-}" = "free_api" ] && [ -n "${SETTINGS_FILE:-}" ] && [ -f "$SETTINGS_FILE" ] && [ -s "$SETTINGS_FILE" ]; then
        if [[ -n "$_overlay_settings" ]]; then
            # Two overlays: merge blind-trust + remote settings first, then add generated
            _tmp_merge="$(
                la_mktemp_json "${TMPDIR:-/tmp}/free-agents-merged-settings1"
            )"
            chmod 600 "$_tmp_merge"
            LAUNCH_DIR="$SCRIPT_DIR" "$SCRIPT_DIR/merge-settings.py" \
                --base "$_base_settings" \
                --overlay "$_overlay_settings" \
                --output "$_tmp_merge" 2>/dev/null || true
            if [ -f "$_tmp_merge" ] && [ -s "$_tmp_merge" ]; then
                _base_settings="$_tmp_merge"
            fi
            _overlay_settings="$SETTINGS_FILE"
        else
            _overlay_settings="$SETTINGS_FILE"
        fi
    fi

    # Apply final overlay if any
    if [[ -n "$_overlay_settings" ]]; then
        FINAL_SETTINGS_FILE="$(
            la_mktemp_json "${TMPDIR:-/tmp}/free-agents-merged-settings"
        )"
        chmod 600 "$FINAL_SETTINGS_FILE"
        LAUNCH_DIR="$SCRIPT_DIR" "$SCRIPT_DIR/merge-settings.py" \
            --base "$_base_settings" \
            --overlay "$_overlay_settings" \
            --output "$FINAL_SETTINGS_FILE" 2>/dev/null || true
        if [ -f "$FINAL_SETTINGS_FILE" ] && [ -s "$FINAL_SETTINGS_FILE" ]; then
            claude_cmd+=(--settings "$FINAL_SETTINGS_FILE")
        else
            # Fallback: use blind-trust only
            claude_cmd+=(--settings "$BLIND_TRUST_SETTINGS_FILE")
        fi
    else
        # Only blind-trust settings
        claude_cmd+=(--settings "$BLIND_TRUST_SETTINGS_FILE")
    fi
elif [[ -n "$REMOTE_CLAUDE_SETTINGS_FILE" ]]; then
    # Only LA_REMOTE_CLAUDE_SETTINGS (no blind-trust)
    claude_cmd+=(--settings "$REMOTE_CLAUDE_SETTINGS_FILE")
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
# LA_AUTO_COMPACT_WINDOW: pass --autocompact to claude if set
if [[ -n "${LA_AUTO_COMPACT_WINDOW:-}" ]]; then
    claude_cmd+=(--autocompact "$LA_AUTO_COMPACT_WINDOW")
fi
if [[ ${#PASSTHRU[@]} -gt 0 ]]; then
    claude_cmd+=("${PASSTHRU[@]}")
fi

# SESSION NAME — DO NOT set at launch. Let Claude Code auto-generate the session name
# from the first prompt, then our UserPromptSubmit hook will prepend the prefix.
# This gives us: "<emoji> <model> <auto-generated-name>"
# The hook reads LA_SESSION_KIND_EMOJI and MODEL_ALIAS from the environment.
# SESSION_NAME="${LA_SESSION_KIND_EMOJI:-$SESSION_EMOJI_FREE_API} ${MODEL}"
# claude_cmd=( "${claude_cmd[0]}" -n "$SESSION_NAME" "${claude_cmd[@]:1}" )

# Session-scoped GIT_CONFIG_* to silence git credential.helper 100001 (Keychain trust prompt)
# inside sandbox, WITHOUT losing the github.com gh helper (an empty credential.helper alone
# wiped it, so pushes failed "could not read Username"). Only affects this process tree.
# See bin/la-git-credential-env.sh and memory: git-keychain-100001-and-gh-tls-under-sandbox
# shellcheck source=la-git-credential-env.sh
. "$SCRIPT_DIR/la-git-credential-env.sh"

# rtk OFF for free-API sessions. rtk's global PreToolUse hook (`rtk hook claude`) condenses Bash
# output to save tokens; a free lane has no token bill, and the condensing truncated
# `git show <rev>:<file>` badly enough that models rewrote CHANGELOG/README from a partial view.
# The shim answers that hook with "no rewrite" and passes any other rtk call to the real binary.
# Harmless when rtk is not installed. LA_RTK_IN_FREE_API=1 keeps rtk on. See bin/rtk-lane-shim/rtk.
if [[ "${LA_SESSION_KIND:-}" == "free_api" && "${LA_RTK_IN_FREE_API:-0}" != "1" ]]; then
    export LA_RTK_HOOK=off
    export PATH="$SCRIPT_DIR/rtk-lane-shim:$PATH"
fi

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
