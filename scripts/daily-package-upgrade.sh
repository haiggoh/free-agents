#!/usr/bin/env bash
# daily-package-upgrade.sh — DAILY launchd agent for Homebrew + pipx upgrades.
# Runs: brew upgrade && brew upgrade --greedy && pipx upgrade-all
# Logs to ~/.claude/logs/daily-package-upgrade.log with timestamps.
# Designed for launchd (StartCalendarInterval daily); safe to run multiple times.
#
# Why a single agent: both brew and pipx are wall-clock maintenance tasks that
# benefit from running together (one notification, one log line per run).
# If either fails, the other still runs; we log each outcome separately.
# Usage: daily-package-upgrade.sh [--help]       (no other arguments)
# Environment: HOME  log goes to $HOME/.claude/logs/daily-package-upgrade.log
set -uo pipefail
case "${1:-}" in
    "") ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *) echo "usage: daily-package-upgrade.sh [--help]" >&2; exit 2 ;;
esac

LOG="$HOME/.claude/logs/daily-package-upgrade.log"
mkdir -p "$(dirname "$LOG")"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log() { echo "$(ts) $*" >> "$LOG"; }

run_step() {
    local label="$1"; shift
    log "START $label: $*"
    if "$@" 2>&1 | while IFS= read -r line; do log "  $label: $line"; done; then
        log "OK    $label"
        return 0
    else
        local rc=$?
        log "FAIL  $label (exit $rc)"
        return $rc
    fi
}

log "=== daily-package-upgrade run ==="

# brew upgrade (non-greedy first: core formulae)
run_step brew brew upgrade

# brew upgrade --greedy (includes casks and pinned formulae)
run_step "brew --greedy" brew upgrade --greedy

# pipx upgrade-all (all pipx-installed CLI tools)
if command -v pipx >/dev/null 2>&1; then
    run_step pipx pipx upgrade-all
else
    log "SKIP  pipx (not installed)"
fi

log "=== daily-package-upgrade done ==="
exit 0