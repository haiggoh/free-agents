#!/usr/bin/env python3
"""fa_notify.py — the ONE notification helper (0.55.8 Task 0, 0541 plan Item H).

Every user-facing notification goes through notify(); no script may call
osascript/terminal-notifier directly (tests/test_no_real_notifications_0541.py scans for
strays). FA_NOTIFY_SINK: when set, each notification is appended to that file as one JSON
line {"title", "text", "caller"} and NOTHING is sent — this is the test fence, so tests can
prove silence. A SET sink whose parent directory does not exist fails loudly (exit 2) naming
the variable: an override that cannot work must never fall through to a real notification.

CLI: fa_notify.py <title> <text> [--caller PATH]   (--help shows this; unknown flags exit 2)
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def _caller():
    """Best-effort: the first frame outside this file (the script that asked to notify)."""
    import inspect
    frame = inspect.currentframe()
    while frame:
        name = frame.f_globals.get("__file__", "")
        if name and Path(name).name not in ("fa_notify.py", "<string>"):
            return name
        frame = frame.f_back
    return None


def notify(title: str, text: str, caller: str | None = None) -> None:
    """Send (or sink) one notification. Never raises on osascript failure — delivery is
    best-effort, exactly like the raw callers it replaces; only a BROKEN FA_NOTIFY_SINK
    override raises."""
    sink = os.environ.get("FA_NOTIFY_SINK")
    if sink is not None:
        parent = Path(sink).parent
        if not parent.is_dir():
            raise SystemExit(f"fa_notify: FA_NOTIFY_SINK={sink!r} — its directory does not exist")
        record = json.dumps({"title": title, "text": text, "caller": caller or _caller()})
        with open(sink, "a") as fh:
            fh.write(record + "\n")
        return
    safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{safe_text}" with title "{safe_title}"'
    subprocess.run(["osascript", "-e", script], check=False)


def main(argv):
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    caller = None
    if "--caller" in argv:
        i = argv.index("--caller")
        if len(argv) <= i + 1:
            print("fa_notify: --caller needs a value", file=sys.stderr)
            return 2
        caller = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    for flag in ("-h", "--help", "--caller"):
        if flag in argv:
            print(f"fa_notify: unknown/misplaced flag {flag!r}", file=sys.stderr)
            return 2
    stray = [a for a in argv if a.startswith("-")]
    if stray:
        print(f"fa_notify: unknown flag(s): {stray}", file=sys.stderr)
        return 2
    if len(argv) < 2:
        print("usage: fa_notify.py <title> <text> [--caller PATH]  (--help for details)",
              file=sys.stderr)
        return 2
    notify(argv[0], " ".join(argv[1:]), caller)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
