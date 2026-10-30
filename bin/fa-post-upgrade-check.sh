#!/usr/bin/env bash
# bin/fa-post-upgrade-check.sh — after ANY package upgrade (brew, pipx, or manual), check every
# registered free-agents backend venv by RUNNING it, and optionally repair the broken ones.
#
# Usage: fa-post-upgrade-check.sh [--repair] [--quiet] [--help]
#   --repair   run `manage-backend.py repair <ver>` (make-before-break) on each broken version
#              that has a snapshot receipt; versions without a receipt are reported, not rebuilt
#   --quiet    suppress the report on stdout (failures still notify and exit 1)
#   --help     print this help and exit 0 — parsed BEFORE anything runs; an unknown flag exits 2
#
# Environment:
#   FA_MANAGE_BACKEND   override the manage-backend.py path (tests only)
#   FA_NOTIFY_CMD       override the failure-notification command (tests only); default is an
#                       osascript "display notification"
#
# Exit codes: 0 = every registered backend healthy (after repairs, with --repair)
#             1 = something is still broken (macOS notification sent)
#             2 = usage error
#
# The weekly launchd job (manage-backend.py launchd install -> backend-check.sh) calls this
# report-only; ~/.claude/scripts/daily-package-upgrade.sh calls it with --repair.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MB="${FA_MANAGE_BACKEND:-$HERE/../install/manage-backend.py}"

usage() {
    awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"
}

REPAIR=0
QUIET=0
for a in "$@"; do
    case "$a" in
        --repair) REPAIR=1 ;;
        --quiet)  QUIET=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'fa-post-upgrade-check: unknown flag: %s\n' "$a" >&2; usage >&2; exit 2 ;;
    esac
done

say() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }
notify() {
    # FA_NOTIFY_CMD is a single executable path (tests stub it); the default is written out
    # literally — an unquoted $VAR expansion never re-parses embedded quotes, which would
    # mangle the osascript argv (found by the 0.49.0 cross-check dispatch)
    if [ -n "${FA_NOTIFY_CMD:-}" ]; then
        "$FA_NOTIFY_CMD" >/dev/null 2>&1 || true
    else
        osascript -e 'display notification "free-agents: a backend venv is broken — run manage-backend.py health --all" with title "free-agents backend check"' >/dev/null 2>&1 || true
    fi
}

TMPF="$(mktemp "${TMPDIR:-/tmp}/fa-puc-health.XXXXXX")"
trap 'rm -f "$TMPF"' EXIT

# health --all exits 1 when something is broken — expected; an EMPTY document means the
# checker itself failed, and that must fail loudly, not report clean.
python3 "$MB" health --all --json >"$TMPF" 2>/dev/null
if [ ! -s "$TMPF" ]; then
    say "FAIL: manage-backend.py health --all produced no output"
    notify
    exit 1
fi

# One line per BROKEN (and not leftover) version: backend|version|has_receipt
rows="$(python3 - "$TMPF" "$HOME" <<'PYEOF'
import json, pathlib, sys
rows = json.load(open(sys.argv[1]))
home = pathlib.Path(sys.argv[2])
# receipt path per the managers' own convention: <home>/.cache/local-agents/rapid-runtime-manager/receipts/<backend>-<version>.json
for r in rows:
    if r.get("state") != "broken" or r.get("leftover") or r.get("owner") == "pipx":
        continue
    receipt = home / ".cache" / "local-agents" / "rapid-runtime-manager" / "receipts" / f"{r['backend']}-{r['version']}.json"
    print(f"{r['backend']}|{r['version']}|{1 if receipt.is_file() else 0}")
PYEOF
)"

if [ -z "$rows" ]; then
    say "all registered backends healthy"
    exit 0
fi

if [ "$REPAIR" -eq 1 ]; then
    printf '%s\n' "$rows" | while IFS='|' read -r be ver has_receipt; do
        if [ "$has_receipt" = "1" ]; then
            say "repairing $be $ver (receipt present)"
            python3 "$MB" --backend "$be" repair "$ver" >/dev/null 2>&1 || say "repair FAILED for $be $ver"
        else
            say "broken $be $ver has no receipt — not rebuilt; repair manually"
        fi
    done
    if python3 "$MB" health --all >/dev/null 2>&1; then
        say "all backends healthy after repair"
        exit 0
    fi
fi
say "BROKEN backends remain:"
printf '%s\n' "$rows" | while IFS='|' read -r be ver _r; do say "  $be $ver — repair: python3 $MB --backend $be repair $ver"; done
notify
exit 1
