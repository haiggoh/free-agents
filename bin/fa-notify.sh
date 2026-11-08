#!/bin/sh
# fa-notify.sh <title> <text> — the shell front end of bin/fa_notify.py (0.55.8 Task 0).
# Resolves the helper relative to THIS file so a foreign CWD works (R15), and inherits
# fa_notify's FA_NOTIFY_SINK contract: a set sink records the notification instead of
# sending it; a set-but-unreachable sink fails loudly naming the variable.
set -eu
if [ "$#" -lt 2 ] || [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    echo "fa-notify.sh <title> <text> — the shell front end of bin/fa_notify.py."
    echo "Honours FA_NOTIFY_SINK: a set sink records {title,text,caller} instead of sending."
    [ "${1:-}" = "--help" ] || exit 2
    exit 0
fi
here="$(cd -P "$(dirname "$0")" && pwd)"
exec python3 "$here/fa_notify.py" "$1" "$2" --caller "$0"
