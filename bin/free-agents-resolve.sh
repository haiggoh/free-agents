#!/usr/bin/env sh
# free-agents-resolve.sh — resolve where this plugin lives at runtime.
#
# Purpose: the plugin's own binaries need to know where the installed copy is
# so they can point at the right place for the config, and run-tests can
# locate the installed scripts. The path is NOT fixed at write-time: the user
# may have cloned the repo somewhere that isn't ~/ClaudeWorkspace/free-agents,
# and a fresh install comes from the plugin cache which also has no stable
# absolute path (the version subdirectory changes with every release).
#
# Resolution order:
#   1. $FREE_AGENTS_PLUGIN   — explicit override (developer/sysadmin pin)
#   2. $CLAUDE_PLUGIN_ROOT   — Claude's plugin environment variable
#   3. Plugin cache           — $CLAUDE_PLUGIN_ROOT or ~/.claude/plugins/cache/haiggoh/free-agents
#   4. Script directory       — $(dirname "$0")/.. (source checkout workflow)
#
# Output: prints exactly one absolute path to stdout (the resolved PLUGIN_DIR)
# and exits 0. Prints nothing on stderr. Never creates or modifies anything.
#
# This is NOT sourced by the binaries — it is a standalone helper that the
# install scripts and tests call. The binaries that need the plugin dir
# (session-menu local storage setup) resolve it inline so they do not depend
# on the helper being on PATH.
#
set -e
[ -n "${FREE_AGENTS_DEBUG:-}" ] && set -x

resolve() {
    # 1. Explicit override
    if [ -n "${FREE_AGENTS_PLUGIN:-}" ]; then
        if [ -f "$FREE_AGENTS_PLUGIN/.claude-plugin/plugin.json" ]; then
            printf '%s\n' "$FREE_AGENTS_PLUGIN"
            return 0
        fi
        # Invalid override — die loudly so the user notices before anything breaks.
        printf 'free-agents-resolve: FREE_AGENTS_PLUGIN=%s exists but is not a plugin dir (no .claude-plugin/plugin.json)\n' \
            "$FREE_AGENTS_PLUGIN" >&2
        return 1
    fi

    # 2. Plugin cache location (CLAUDE_PLUGIN_ROOT from environment or default)
    _cache_base="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/plugins/cache}/haiggoh/free-agents"
    if [ -d "$_cache_base" ]; then
        # Pick the highest-versioned subdirectory (0.8.0 beats 0.7.9)
        _hit=""
        for _d in "$_cache_base"/*/; do
            [ -f "$_d/.claude-plugin/plugin.json" ] || continue
            if [ -z "$_hit" ] || _d_ge "$_d" "$_hit"; then
                _hit="$_d"
            fi
        done
        if [ -n "$_hit" ]; then
            # Strip trailing slash for clean path
            printf '%s\n' "${_hit%/}"
            return 0
        fi
    fi

    # 3. The directory containing this script — the developer checkout path.
    _here="$(cd -P "$(dirname "$0")" && pwd)"
    _here="$(cd -P "$_here/.." && pwd)"
    if [ -f "$_here/.claude-plugin/plugin.json" ]; then
        printf '%s\n' "$_here"
        return 0
    fi

    # Fallback: still return a path even if it doesn't look like a plugin
    printf '%s\n' "$_here"
}

# Numeric version comparison: returns 0 if $1 >= $2.
# Handles N.M, N.M.P and N.M.P.Q via dot-split, padding with zeros.
_dotted_to_int() {
    printf '%s\n' "$1" | awk -F. '{printf "%05d%05d%05d%05d\n", $1+0, $2+0, $3+0, $4+0}'
}

_d_ge() {
    _a=$(_dotted_to_int "${1##*/}")
    _b=$(_dotted_to_int "${2##*/}")
    [ "$_a" -ge "$_b" ]
}

# Parse arguments BEFORE resolving: `--help` used to be ignored, so probing the script printed a
# path and exited 0 as if it were help (0.34.4). An unknown flag is an error, never a silent run.
case "${1:-}" in
    -h|--help)
        cat <<'EOF'
free-agents-resolve.sh — print the absolute path of this plugin's directory, then exit 0.

Usage: free-agents-resolve.sh [--help]

Resolution order: $FREE_AGENTS_PLUGIN (must contain .claude-plugin/plugin.json), then the
highest installed version in the plugin cache (under $CLAUDE_PLUGIN_ROOT when set), then this
script's own checkout. Read-only: creates and modifies nothing.

Environment:
  FREE_AGENTS_PLUGIN   explicit plugin dir (an invalid one is an error, exit 1)
  CLAUDE_PLUGIN_ROOT   plugin cache root to search
  FREE_AGENTS_DEBUG    non-empty: trace with set -x
EOF
        exit 0 ;;
    "") ;;
    *) printf 'free-agents-resolve.sh: unknown argument: %s (try --help)\n' "$1" >&2; exit 2 ;;
esac

resolve