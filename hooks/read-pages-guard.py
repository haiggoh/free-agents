#!/usr/bin/env python3
"""read-pages-guard.py — PreToolUse hook on `Read`: deny `pages` on non-PDF paths (0.49.3, H6).

WHY: Read's `pages` is PDF-only. On a text file it is SILENTLY IGNORED and OVERRIDES
`offset`/`limit` (the read restarts at line 1 with no error) — models that try it once
(notably Step 5 Preview sessions) loop on the mistake. A prose rule in the system prompt
did not stop it; this mechanical deny does.

WHY ALWAYS-ON (not per-model): the misuse is invalid for EVERY model — a `pages` key on a
non-PDF path can never do what the caller meant — so per-model gating has zero benefit and a
spoofed-model-id gate would be fragile. Never denies a valid Read:
  * PDF + pages            -> allow
  * text + offset/limit     -> allow
  * other tools / no pages  -> allow
  * unparseable stdin       -> allow (fail-open: a guard that blocks everything when it
                               breaks is worse than the bug it prevents)

The deny message gives the offset/limit equivalent to re-issue with (the caller's own
offset/limit if present, else defaults), because `pages` was overriding them.

Hook protocol: PreToolUse payload as JSON on stdin {"tool_name": ..., "tool_input": {...}}.
Deny = JSON decision on stdout + exit 2; allow = exit 0 with no output. `--self-test` runs the
built-in cases; `--help` prints this text.
"""
import json
import sys

DEFAULT_LIMIT = 2000


def decide(payload) -> tuple:
    """-> (deny: bool, reason: str). Pure; testable without the hook protocol."""
    if not isinstance(payload, dict) or payload.get("tool_name") != "Read":
        return False, ""
    ti = payload.get("tool_input")
    if not isinstance(ti, dict) or "pages" not in ti:
        return False, ""
    path = ti.get("file_path")
    if not isinstance(path, str) or not path:
        return False, ""
    if path.lower().endswith(".pdf"):
        return False, ""  # valid use: pages is the PDF pager
    offset = ti.get("offset") or 1
    limit = ti.get("limit") or DEFAULT_LIMIT
    reason = (
        f"Read's `pages` is PDF-only; on '{path}' it is silently ignored and OVERRIDES "
        f"offset/limit (the read restarts at line 1, no error). Re-issue with "
        f"offset={offset} limit={limit} — for text files, paging is line-based via "
        f"offset/limit, not pages."
    )
    return True, reason


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    if "--self-test" in sys.argv:
        cases = [
            ({"tool_name": "Read", "tool_input": {"file_path": "/x/a.md", "pages": "2-4"}}, True),
            ({"tool_name": "Read", "tool_input": {"file_path": "/x/a.pdf", "pages": "1-3"}}, False),
            ({"tool_name": "Read", "tool_input": {"file_path": "/x/a.md", "offset": 50, "limit": 100}}, False),
            ({"tool_name": "Read", "tool_input": {"file_path": "/x/a.md", "pages": "2", "offset": 50}}, True),
            ({"tool_name": "Bash", "tool_input": {"command": "ls"}}, False),
            ({"tool_name": "Read", "tool_input": {"file_path": "/x/a.md"}}, False),
            ("{not json", False),  # fail-open on unparseable input
        ]
        ok = True
        for payload, want_deny in cases:
            try:
                data = json.loads(payload) if isinstance(payload, str) else payload
            except Exception:
                data = None
            deny, _ = decide(data)
            status = "PASS" if deny == want_deny else "FAIL"
            ok = ok and deny == want_deny
            print(f"{status}: deny={deny} want={want_deny} payload={str(payload)[:60]}")
        return 0 if ok else 1
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open
    deny, reason = decide(payload)
    if not deny:
        return 0
    print(json.dumps({
        "decision": "block",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }))
    return 2


if __name__ == "__main__":
    sys.exit(main())
