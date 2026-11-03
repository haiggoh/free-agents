#!/usr/bin/env bash
# launch-remote-window.sh — deterministic INTERACTIVE launcher for a free remote session window.
#
# A handover/successor session must be a real interactive Terminal window, never a headless
# background launch from an agent Bash tool (measured 2026-10-25: headless launches produced
# idle/empty windows). This wrapper hard-requires a TTY and refuses with a one-line message
# when there is none; with a TTY it execs `csl remote "$@"` so the window is owned by the
# user's Terminal and returns to it when the session ends.
#
# Usage: launch-remote-window.sh <alias> [--effort low|medium|high|xhigh|max] [--prompt TEXT]
#
# Environment:
#   LAUNCH_REMOTE_WINDOW_CSL   override the csl binary to exec (default: this repo's bin/csl)
#
# Exit codes: 0 launched (csl's own exit), 2 usage error or headless refusal, 3 unknown flag.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'USAGE'
launch-remote-window.sh — start a free remote session in a REAL interactive window

Usage: launch-remote-window.sh <alias> [--effort low|medium|high|xhigh|max] [--prompt TEXT]

Hard-requires a TTY: from an agent Bash tool (no TTY) it refuses with a one-line message
and exits non-zero instead of starting a headless session. With a TTY it execs
`csl remote "$@"`.

Environment:
  LAUNCH_REMOTE_WINDOW_CSL   csl binary to exec (default: this repo's bin/csl)

Exit codes: 0 launched; 2 usage error / headless refusal; 3 unknown flag.
USAGE
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    "") usage >&2; exit 2 ;;
esac

# Pass-through flags only; anything else starting with '-' is a usage error (never
# silently swallowed into the launch).
KNOWN_FLAGS=(--effort --prompt --dry-run --list)
prev=""
for a in "$@"; do
    case "$a" in
        -*) case "$a" in
                --effort|--prompt|--dry-run|--list) ;;
                *) echo "launch-remote-window.sh: unknown flag: $a" >&2; usage >&2; exit 3 ;;
            esac ;;
    esac
    prev="$a"
done

if [ ! -t 0 ]; then
    echo "launch-remote-window.sh: refusing to start a headless session — run this in Terminal.app (interactive), not from an agent Bash tool." >&2
    exit 2
fi

CSL="${LAUNCH_REMOTE_WINDOW_CSL:-$HERE/csl}"
exec "$CSL" remote "$@"
