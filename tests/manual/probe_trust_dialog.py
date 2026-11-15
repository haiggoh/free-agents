#!/usr/bin/env python3
"""Launch a real `claude` in a tmux-managed PTY and report whether the folder-trust
dialog appears.

One probe per invocation. Creates a DETACHED tmux session that runs
`claude --strict-mcp-config --mcp-config '{"mcpServers":{}}'` with NO prompt in the
given directory, polls `tmux capture-pane` for up to --timeout seconds, prints DIALOG
when the captured screen contains BOTH "trust" and "folder" (case-insensitive), else
NO_DIALOG. It NEVER answers the dialog and never writes any config file. Cleanup kills
ONLY its own tmux session (which terminates the claude it hosted); a tmux server that
hosts other sessions is never touched.

Mechanism note (2026-11-15): this development harness's sandbox breaks in-process
forkpty for spawns of `claude` (fork returns a bogus pid with no reapable child), so
the probe cannot pty.fork claude itself; the tmux server, a normal native process,
owns the pty instead. Outside such a sandbox plain pty.fork would work equally well.

Usage:
  probe_trust_dialog.py <dir> [--timeout SECONDS] [--show] [--json]
                        [--env KEY=VALUE ...]
  probe_trust_dialog.py --help

Options:
  <dir>                Directory to launch `claude` in (tmux -c starts the session there).
  --timeout SECONDS    Max seconds to poll the screen before classifying (default 20).
  --show               Print the last captured screen after the verdict (UI text only).
  --json               Print {"dir","verdict","elapsed_s","session_alive"} as JSON.

Exit codes: 0 probe ran (verdict DIALOG or NO_DIALOG), 2 usage error,
4 tmux launch failed. Environment: FA_PROBE_DEBUG=1 prints the tmux session name.
"""
import argparse
import json
import os
import subprocess
import sys
import time

DIALOG_WORDS = ("trust", "folder")
POLL_S = 0.5
TMUX = "tmux"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="probe_trust_dialog.py",
        description="Probe whether `claude` shows the folder-trust dialog in a directory "
        "(one real PTY launch per run via a private tmux session; never answers the dialog).",
    )
    ap.add_argument("dir", help="directory to launch claude in")
    ap.add_argument("--timeout", type=float, default=20.0,
                    help="max seconds to poll the screen before classifying (default 20)")
    ap.add_argument("--show", action="store_true",
                    help="print the last captured screen after the verdict")
    ap.add_argument("--json", action="store_true",
                    help="print the result as a JSON object")
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                    help="environment override for the claude process, repeatable "
                    "(travel inside the tmux command, so the tmux server's own env "
                    "cannot drop them)")
    return ap


def tmux(*args: str, timeout: float = 15.0) -> subprocess.CompletedProcess:
    return subprocess.run([TMUX, *args], capture_output=True, text=True, timeout=timeout)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.isdir(args.dir):
        print(f"probe_trust_dialog: not a directory: {args.dir}", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("probe_trust_dialog: --timeout must be > 0", file=sys.stderr)
        return 2

    session = f"fa-trust-probe-{os.getpid()}-{int(time.time())}"
    # shell-quoted claude command: NO prompt, empty MCP config; --env overrides prefix it
    env_prefix = " ".join(f"{kv}" for kv in args.env if "=" in kv)
    claude_cmd = (" ".join([env_prefix, "claude --strict-mcp-config",
                            "--mcp-config", "'{\"mcpServers\":{}}'"]).strip())
    new = tmux("new-session", "-d", "-s", session, "-c", args.dir, claude_cmd)
    if new.returncode != 0:
        print(f"probe_trust_dialog: tmux new-session failed: {new.stderr.strip()}",
              file=sys.stderr)
        return 4
    if os.environ.get("FA_PROBE_DEBUG"):
        print(f"debug: tmux session={session}", file=sys.stderr, flush=True)

    text = ""
    session_alive = True
    start = time.monotonic()
    deadline = start + args.timeout
    while time.monotonic() < deadline:
        cap = tmux("capture-pane", "-p", "-t", session)
        if cap.returncode != 0:
            session_alive = False  # session gone: claude exited on its own
            break
        text = cap.stdout
        low = text.lower()
        if all(w in low for w in DIALOG_WORDS):
            break
        time.sleep(POLL_S)
    elapsed = time.monotonic() - start

    # kill ONLY our own session; a shared tmux server is left untouched
    tmux("kill-session", "-t", session)

    verdict = "DIALOG" if all(w in text.lower() for w in DIALOG_WORDS) else "NO_DIALOG"
    if args.json:
        print(json.dumps({"dir": args.dir, "verdict": verdict,
                          "elapsed_s": round(elapsed, 1),
                          "session_alive": session_alive}))
    else:
        print(f"{verdict} (elapsed {elapsed:.1f}s, session_alive={session_alive})")
    if args.show:
        sys.stdout.write(text)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
