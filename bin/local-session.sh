#!/usr/bin/env bash
# local-session.sh — canonical direct launcher for LOCAL sessions.
# Provides both direct alias launch and no-arg picker entry point.
#
# Usage:
#   local-session.sh                      # session picker, Local screen (Quit, no Back)
#   local-session.sh <alias> [effort]     # launch that local agent directly
#   local-session.sh --help               # show this help
#   local-session.sh --dry-run <alias>    # show plan without launching
#   local-session.sh --inventory          # machine-readable roster for picker (TSV)
#
# Environment:
#   CSL_SELF              override csl path (default: bin/csl)
#   CSL_DIR               override bin directory
#   LA_ENABLE_MCP=1       Enable MCPs for this session (default: 0)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source config and shared emoji
# shellcheck source=/dev/null
. "$SCRIPT_DIR/../config/config-lib.sh"
# shellcheck source=/dev/null
. "$SCRIPT_DIR/../config/emoji.sh"
la_load_config || exit 2

usage() {
    cat <<'HELP'
local-session.sh — launch a LOCAL Claude Code session (MLX inference on this machine)

Usage:
  local-session.sh                      # Local Session Picker directly (Quit, no Back)
  local-session.sh <alias> [effort]     # Direct launch (no picker)
  local-session.sh --help               # Show this help
  local-session.sh --dry-run <alias> [effort]  # Validate and show plan, don't launch
  local-session.sh --inventory          # Print session-capable local models as TSV

Positional arguments:
  <alias>          Model alias from config (e.g., qwen-3.8-operator, deepseek-r1-architect)
                   Role names also accepted: operator, reasoner, validator, utility
  [effort]         Optional: low | medium | high | xhigh | max (overrides config default)

Flags:
  --help                  Show this help and exit
  --dry-run               Validate config, show resolved model/backend/effort/port, do NOT launch
  --dry-run-skip-preflight  Dry-run without RAM preflight (quick config inspection)
  --enable-mcp            Enable MCP tools in blind-trust auto mode (sets LA_ENABLE_MCP=1)
  --inventory             Print session-capable local models as TSV for the picker, then exit

Environment (set by csl or caller):
  LA_AUTO_MODE=1                    Enable auto mode (permission-mode=auto)
  LA_BLIND_AUTO=1                   Blind-trust auto mode (no classifier)
  LA_TELEMETRY=0|1                  Disable/enable nonessential outbound traffic (default 0)
  LA_QUEUE_STOP_HOOK=0|1            Queued-prompt stop hook (default 0)
  LA_STRICT_MCP=true|false          Exclude MCP servers from prompt (default true)
  LA_SKIP_RAM_PREFLIGHT=1           Bypass RAM check (not recommended)
  LA_DRY_RUN_SKIP_PREFLIGHT=1       Skip RAM preflight in --dry-run (quick config inspection)
  LA_ENABLE_MCP=1                   Enable MCP tools in blind-trust auto mode (default 0)
                                    Ignored when LA_STRICT_MCP=true (MCP servers excluded from prompt)

Examples:
  local-session.sh
  local-session.sh qwen-3.8-operator
  local-session.sh operator high
  local-session.sh --dry-run deepseek-r1-architect max
  local-session.sh --dry-run-skip-preflight qwen-3.8-operator
  local-session.sh --enable-mcp qwen-3.8-operator

HELP
}

# ---- argument parsing (BEFORE any work — --help must never launch anything) ----
MODE="launch"
ALIAS=""
EFFORT_OVERRIDE=""
DRY_RUN=0
INVENTORY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)        usage; exit 0 ;;
        --dry-run)        DRY_RUN=1; shift ;;
        --dry-run-skip-preflight)
            DRY_RUN=1
            export LA_DRY_RUN_SKIP_PREFLIGHT=1
            shift ;;
        --enable-mcp)     export LA_ENABLE_MCP=1; shift ;;
        --inventory)      INVENTORY=1; shift ;;
        -?*)              echo "local-session.sh: unrecognised option: $1; use --help" >&2; exit 2 ;;
        *)                if [[ -z "$ALIAS" ]]; then ALIAS="$1"; else EFFORT_OVERRIDE="$1"; fi; shift ;;
    esac
done

# Handle inventory mode - output TSV for picker
if [[ $INVENTORY -eq 1 ]]; then
    # TSV: alias, backend, configured effort, roles, explicit family
    for a in "${LA_ALIASES[@]}"; do
        case "${LA_SERVE[$a]:-}" in
            rapid|vllm)
                la_on_disk "$a" || continue
                printf '%s\t%s\t%s\t%s\t%s\n' "$a" "$(la_serve_display "$a")" "${LA_EFFORT[$a]:-}" \
                  "$(la_roles_for_alias "$a")" ""
                ;;
        esac
    done
    exit 0
fi

# If no alias given, delegate to the shared picker at Local screen as DIRECT ROOT
if [[ -z "$ALIAS" ]]; then
    if [[ ! -t 0 || ! -t 1 ]]; then
        echo "local-session.sh: no alias given and no terminal for the picker; pass an alias or use csl." >&2
        exit 2
    fi
    exec "$SCRIPT_DIR/session-picker" local
fi

# Resolve alias - accept role names as aliases
case "$ALIAS" in
    operator|reasoner|validator|utility)
        # Find alias by role
        for a in "${LA_ALIASES[@]}"; do
            roles="$(la_roles_for_alias "$a")"
            if [[ "$roles" == *"$ALIAS"* ]]; then
                ALIAS="$a"
                break
            fi
        done
        ;;
esac

# Validate alias exists and is session-capable
if [[ -z "${LA_SERVE[$ALIAS]:-}" ]]; then
    echo "local-session.sh: unknown alias: $ALIAS" >&2
    la_aliases_help >&2
    exit 2
fi
case "${LA_SERVE[$ALIAS]}" in
    rapid|vllm) ;;
    *) echo "local-session.sh: alias '$ALIAS' is not session-capable (serve=${LA_SERVE[$ALIAS]:-none})" >&2; exit 2 ;;
esac
la_on_disk "$ALIAS" || { echo "local-session.sh: model '$ALIAS' is not on disk" >&2; exit 1; }

# Apply effort override if given
if [[ -n "$EFFORT_OVERRIDE" ]]; then
    case "$EFFORT_OVERRIDE" in
        low|medium|high|xhigh|max) LA_EFFORT[$ALIAS]="$EFFORT_OVERRIDE" ;;
        *) echo "local-session.sh: invalid effort '$EFFORT_OVERRIDE'; use low|medium|high|xhigh|max" >&2; exit 2 ;;
    esac
fi

# Apply session profile (manifest-driven autocompaction, etc.)
# This mirrors csl's _apply_session_profile
_apply_session_profile() {
    local alias="$1"
    local explicit_override="${LA_SESSION_AUTO_COMPACT[$alias]:-}"

    if [ -n "$explicit_override" ]; then
        export LA_AUTO_COMPACT_WINDOW="$explicit_override"
        return
    fi

    # Try manifest-driven derivation
    local manifest_json
    manifest_json="$(la_load_manifest "$alias")"
    if [ -n "$manifest_json" ]; then
        local effective
        effective="$(la_manifest_effective_context "$manifest_json")"
        if [ -n "$effective" ]; then
            local autocompact
            autocompact="$(la_manifest_autocompaction "$effective")"
            if [ -n "$autocompact" ]; then
                export LA_AUTO_COMPACT_WINDOW="$autocompact"
                return
            fi
        fi
    fi

    # Fallback to LA_MAX_MODEL_LEN
    if [ "${LA_MAX_MODEL_LEN:-0}" -ge 100000 ]; then
        local fallback_floor=$(( (LA_MAX_MODEL_LEN / 100000) * 100000 ))
        if [ "$fallback_floor" -gt 1000000 ]; then
            fallback_floor=1000000
        fi
        export LA_AUTO_COMPACT_WINDOW="$fallback_floor"
    fi
}
_apply_session_profile "$ALIAS"

# DRY-RUN: show plan and exit
if [[ $DRY_RUN -eq 1 ]]; then
    echo "=== DRY RUN — local-session.sh configuration ==="
    echo "Alias: $ALIAS"
    echo "Backend: $(la_serve_display "$ALIAS")"
    echo "Effort: ${LA_EFFORT[$ALIAS]:-?}"
    echo "Thinking: ${LA_THINK[$ALIAS]:-?}"
    roles="$(la_roles_for_alias "$ALIAS")"
    [ -n "$roles" ] && echo "Roles: $roles" || echo "Roles: untagged"
    manifest_json="$(la_load_manifest "$ALIAS")"
    if [ -n "$manifest_json" ]; then
        effective="$(la_manifest_effective_context "$manifest_json" 2>/dev/null || echo "")"
        autocompact="$(la_manifest_autocompaction "$effective" 2>/dev/null || echo "")"
        if [ -n "$effective" ]; then
            echo "Effective context: $effective"
            [ -n "$autocompact" ] && echo "Autocompaction: $autocompact"
        fi
    fi
    echo "Autocompaction window: ${LA_AUTO_COMPACT_WINDOW:-<none>}"
    echo "Auto-mode: $([ "${LA_AUTO_MODE:-0}" = "1" ] && echo "ON" || echo "OFF")"
    echo "Blind-trust: $([ "${LA_BLIND_AUTO:-0}" = "1" ] && echo "ON" || echo "OFF")"
    echo "Telemetry: $([ "${LA_TELEMETRY:-0}" = "1" ] && echo "ON" || echo "OFF")"
    echo "Stop hook: $([ "${LA_QUEUE_STOP_HOOK:-0}" = "1" ] && echo "ON" || echo "OFF")"
    echo "MCPs: $([ "${LA_ENABLE_MCP:-0}" = "1" ] && echo "ENABLED" || echo "DISABLED")"
    echo "Launcher: $SCRIPT_DIR/launch-claude-agent.sh"
    exit 0
fi

# Direct launch - delegate to launch-claude-agent.sh
exec "$SCRIPT_DIR/launch-claude-agent.sh" "$ALIAS" "${LA_EFFORT[$ALIAS]}"