#!/usr/bin/env python3
"""fa_fallback_mark.py — the session-scoped "free exhausted" mark (cloud-screen plan Task 4).

When a dispatch's free attempts are honestly exhausted, free-agent-tool.py writes a mark for
ITS session. The cloud-agent-intercept hook consumes it ONCE to let exactly one native paid
call through. Marks are single-use, expire (default 15 minutes) and are keyed by session id,
so a second cloud session's Agent call stays redirected (review focus 1).

Usage (library — the tool and the hook import it):
    write_mark(session_id, attempts, ttl_s=900) -> Path
    consume_mark(session_id, now=None) -> dict | None   # returns and deletes when unexpired

Env:
    FA_FALLBACK_MARK_DIR  mark directory (default ~/.claude/free-agents/paid-fallback)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def mark_dir() -> Path:
    return Path(os.environ.get("FA_FALLBACK_MARK_DIR")
                or Path.home() / ".claude" / "free-agents" / "paid-fallback")


def mark_path(session_id: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in session_id)
    return mark_dir() / f"{safe}.json"


def write_mark(session_id: str, attempts: list, ttl_s: int = 900, now: float | None = None) -> Path:
    """Record that THIS session's free attempts are exhausted; the mark lives ttl_s seconds."""
    p = mark_path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"session_id": session_id, "attempts": attempts,
                       "written_at": time.time() if now is None else now, "ttl_s": ttl_s}, indent=1) + "\n"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    return p


def consume_mark(session_id: str, now: float | None = None) -> dict | None:
    """Return the session's unexpired mark and delete it (single use); None when missing,
    expired, or unreadable — a corrupt mark is treated as absent, never as permission."""
    p = mark_path(session_id)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    now = time.time() if now is None else now
    if not isinstance(doc, dict) or doc.get("session_id") != session_id \
            or now - doc.get("written_at", 0) > doc.get("ttl_s", 900):
        try:
            p.unlink()
        except OSError:
            pass
        return None
    try:
        p.unlink()
    except OSError:
        pass
    return doc


def _inflight_path(session_id: str, tool_use_id: str) -> Path:
    safe_s = "".join(c if (c.isalnum() or c in "-_") else "_" for c in session_id)
    safe_t = "".join(c if (c.isalnum() or c in "-_") else "_" for c in tool_use_id)
    return mark_dir() / f"{safe_s}.{safe_t}.inflight.json"


def _read_mark(p: Path) -> dict | None:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def claim_mark(session_id: str, tool_use_id: str, now: float | None = None) -> dict | None:
    """0.54.0 Task 8 4b: ATOMICALLY claim the session's mark by os.renaming it to an in-flight
    file keyed by tool_use_id. rename is atomic on one filesystem, so two concurrent
    PreToolUse claims of one mark let exactly ONE through (a read+delete race would let both).
    Returns the mark dict, or None when absent/expired.

    Before claiming, any in-flight file for this session whose TTL has passed is renamed BACK
    to an available mark — that is the case where ANOTHER PreToolUse hook vetoed the call, so
    no PostToolUse will ever arrive to release it, and the one allowed paid call must not be
    burned by a vetoed call."""
    now = time.time() if now is None else now
    p = mark_path(session_id)
    # restore expired in-flight files (the vetoed-call case) so the mark is available again
    try:
        for f in mark_dir().glob("*.inflight.json"):
            if not f.name.startswith("".join(c if (c.isalnum() or c in "-_") else "_" for c in session_id) + "."):
                continue
            doc = _read_mark(f)
            if doc and now - doc.get("claimed_at", doc.get("written_at", 0)) > doc.get("ttl_s", 900):
                # vetoed call: restore the mark as available, refreshed so it is claimable
                # for another full TTL (the mark's own expiry is measured from written_at)
                doc["written_at"] = now
                try:
                    tmp = p.with_suffix(".restore.tmp")
                    tmp.write_text(json.dumps(doc, indent=1))
                    os.rename(f, p)
                    os.replace(tmp, p)
                except OSError:
                    pass
    except OSError:
        pass
    doc = _read_mark(p)
    if not doc or doc.get("session_id") != session_id \
            or now - doc.get("written_at", 0) > doc.get("ttl_s", 900):
        return None
    inflight = _inflight_path(session_id, tool_use_id)
    doc["claimed_at"] = now
    try:
        inflight.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(inflight, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(p, inflight)                 # the ATOMIC claim: second claim finds no mark
    except OSError:
        return None
    return doc


def release_inflight(session_id: str, tool_use_id: str) -> bool:
    """PostToolUse: the claimed call RAN — delete the in-flight file (the mark stays consumed).
    Returns True when an in-flight file was removed."""
    try:
        _inflight_path(session_id, tool_use_id).unlink()
        return True
    except OSError:
        return False


def main(argv=None) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print((__doc__ or "").strip())
        return 0
    print(__doc__.strip().splitlines()[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
