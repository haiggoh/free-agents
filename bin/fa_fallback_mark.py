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


def write_mark(session_id: str, attempts: list, ttl_s: int = 900) -> Path:
    """Record that THIS session's free attempts are exhausted; the mark lives ttl_s seconds."""
    p = mark_path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"session_id": session_id, "attempts": attempts,
                       "written_at": time.time(), "ttl_s": ttl_s}, indent=1) + "\n"
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


def main(argv=None) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print((__doc__ or "").strip())
        return 0
    print(__doc__.strip().splitlines()[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
