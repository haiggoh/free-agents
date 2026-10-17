#!/usr/bin/env python3
"""la-memguard-registry.py — the memory guard's registry of running LOCAL Claude Code sessions.

One JSON file per session under $LA_MEMGUARD_STATE_DIR/sessions/<id>.json. The launchers write it
(register/unregister); the plugin's hooks flip the idle marker (mark); the watchdog reads it (list).

Usage:
  la-memguard-registry.py register --backend B --port P --model M [--tmux-socket PATH --tmux-target T]
                                   [--launcher-pid PID]      prints the new session id
  la-memguard-registry.py unregister ID
  la-memguard-registry.py mark ID --event EVENT            EVENT: Stop | StopFailure | UserPromptSubmit | PostCompact | SessionEnd
  la-memguard-registry.py hook                             hook entry: reads the hook JSON on stdin, marks
                                                           $LA_MEMGUARD_SESSION_ID; a no-op when that is unset
  la-memguard-registry.py list                             JSON array of live sessions (prunes dead launchers)
  la-memguard-registry.py --help

Environment:
  LA_MEMGUARD_STATE_DIR   state root (default ~/.local/state/free-agents/memguard)
  LA_MEMGUARD_SESSION_ID  set by the launcher for the claude process; the hook acts only when it is set
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

IDLE_EVENTS = {"Stop": True, "StopFailure": True, "UserPromptSubmit": False}
# A turn ended by a backend memory abort (oMLX's guard; rapid/llama.cpp out-of-memory refusals).
MEMORY_ABORT_RE = re.compile(r"process memory limit exceeded|out of memory|insufficient memory|"
                             r"memory (?:limit|pressure|guard)|kIOGPUCommandBufferCallbackErrorOutOfMemory", re.I)


def state_dir() -> Path:
    d = os.environ.get("LA_MEMGUARD_STATE_DIR") or os.path.expanduser("~/.local/state/free-agents/memguard")
    return Path(d)


def sessions_dir() -> Path:
    p = state_dir() / "sessions"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


@contextmanager
def _locked(sid: str):
    with open(sessions_dir() / f".{sid}.lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        yield


def _path(sid: str) -> Path:
    if not sid or "/" in sid or sid.startswith("."):
        raise ValueError(f"bad session id {sid!r}")
    return sessions_dir() / f"{sid}.json"


def _write(sid: str, rec: dict) -> None:
    tmp = _path(sid).with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, _path(sid))


def read(sid: str) -> dict | None:
    try:
        return json.loads(_path(sid).read_text())
    except (OSError, ValueError):
        return None


def register(backend: str, port: str, model: str, tmux_socket: str = "", tmux_target: str = "",
             launcher_pid: int | None = None) -> str:
    pid = launcher_pid or os.getppid()
    sid = f"{backend}-{port}-{pid}"
    now = time.time()
    rec = {"id": sid, "backend": backend, "port": str(port), "model": model, "launcher_pid": pid,
           "tmux_socket": tmux_socket, "tmux_target": tmux_target, "started": now,
           "idle": True, "idle_since": now, "last_event": "register", "last_event_at": now}
    with _locked(sid):
        _write(sid, rec)
    return sid


def unregister(sid: str) -> None:
    with _locked(sid):
        _path(sid).unlink(missing_ok=True)
    (sessions_dir() / f".{sid}.lock").unlink(missing_ok=True)


def mark(sid: str, event: str, payload: dict | None = None) -> bool:
    """Record a hook event. Returns False when the session is not registered (nothing written)."""
    with _locked(sid):
        rec = read(sid)
        if rec is None:
            return False
        now = time.time()
        rec["last_event"], rec["last_event_at"] = event, now
        if event in IDLE_EVENTS:
            idle = IDLE_EVENTS[event]
            if idle and not rec.get("idle"):
                rec["idle_since"] = now
            rec["idle"] = idle
            if event == "UserPromptSubmit":
                rec["last_prompt_at"] = now
        if event == "StopFailure" and MEMORY_ABORT_RE.search(json.dumps(payload or {})):
            rec["memory_abort_at"] = now
        elif event == "PostCompact":
            rec["compacted_at"] = now
            # A manual /compact runs between turns, and no Stop follows it: the session is idle
            # again. An auto-compact can run mid-turn, so it leaves the flag alone.
            if (payload or {}).get("trigger") == "manual":
                rec["idle"] = True
        elif event == "SessionEnd":
            rec["ended"] = True
            rec["idle"] = True
        _write(sid, rec)
    return True


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, TypeError, ValueError):
        return isinstance(pid, int)


def live_sessions() -> list[dict]:
    """Every registered session whose launcher is still running; files of dead launchers are removed."""
    out = []
    for f in sorted(sessions_dir().glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not _alive(rec.get("launcher_pid")):
            unregister(rec.get("id") or f.stem)
            continue
        out.append(rec)
    return out


def _usage(code: int) -> int:
    print((__doc__ or "").strip(), file=sys.stdout if code == 0 else sys.stderr)
    return code


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        return _usage(0 if argv else 2)
    cmd, rest = argv[0], argv[1:]
    if cmd == "register":
        opts = {"--backend": "", "--port": "", "--model": "", "--tmux-socket": "", "--tmux-target": "",
                "--launcher-pid": ""}
        it = iter(rest)
        for a in it:
            if a not in opts:
                return _usage(2)
            opts[a] = next(it, "")
        if not (opts["--backend"] and opts["--port"]):
            return _usage(2)
        print(register(opts["--backend"], opts["--port"], opts["--model"], opts["--tmux-socket"],
                       opts["--tmux-target"], int(opts["--launcher-pid"]) if opts["--launcher-pid"] else None))
        return 0
    if cmd == "unregister" and len(rest) == 1:
        unregister(rest[0])
        return 0
    if cmd == "mark" and len(rest) == 3 and rest[1] == "--event":
        return 0 if mark(rest[0], rest[2]) else 1
    if cmd == "hook" and not rest:
        # Hooks must never break the session: every failure here is swallowed, exit 0.
        sid = os.environ.get("LA_MEMGUARD_SESSION_ID", "")
        if not sid:
            return 0
        try:
            payload = json.load(sys.stdin)
            event = payload.get("hook_event_name", "")
            if event:
                mark(sid, event, payload)
        except Exception:
            pass
        return 0
    if cmd == "list" and not rest:
        print(json.dumps(live_sessions(), indent=1))
        return 0
    return _usage(2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
