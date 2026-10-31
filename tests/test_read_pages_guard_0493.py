#!/usr/bin/env python3
"""tests/test_read_pages_guard_0493.py — 0.49.3 (H6): the always-on PreToolUse guard on Read.

`pages` on a non-PDF path is silently ignored by Read AND overrides offset/limit — Step 5
Preview sessions loop on it. The guard denies ONLY that misuse; a valid Read is never denied
(PDF+pages is the planted positive). Fail-open: unparseable stdin allows. Deny message gives
the offset/limit equivalent (the caller's own offset/limit when present)."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "read-pages-guard.py"


def run_hook(payload):
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)


def read_call(**ti):
    return json.dumps({"tool_name": "Read", "tool_input": ti})


class ReadPagesGuardTests(unittest.TestCase):
    def test_md_with_pages_is_denied_with_offset_limit_equivalent(self):
        r = run_hook(read_call(file_path="/tmp/x.md", pages="2-4"))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        decision = json.loads(r.stdout)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
        reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("offset=1 limit=2000", reason, "defaults must be offered when offset/limit absent")

    def test_deny_message_uses_callers_own_offset_limit(self):
        r = run_hook(read_call(file_path="/tmp/x.md", pages="2", offset=50, limit=100))
        self.assertEqual(r.returncode, 2)
        self.assertIn("offset=50 limit=100", json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"])

    def test_pdf_with_pages_is_allowed(self):
        # planted positive: a VALID Read must never be denied
        r = run_hook(read_call(file_path="/tmp/x.pdf", pages="1-3"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_md_with_offset_only_is_allowed(self):
        r = run_hook(read_call(file_path="/tmp/x.md", offset=10, limit=50))
        self.assertEqual(r.returncode, 0)

    def test_other_tools_and_missing_pages_are_allowed(self):
        self.assertEqual(run_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})).returncode, 0)
        self.assertEqual(run_hook(read_call(file_path="/tmp/x.md")).returncode, 0)

    def test_unparseable_stdin_fails_open(self):
        r = run_hook("{not json")
        self.assertEqual(r.returncode, 0, "a broken guard must allow, not wedge the session")

    def test_help_and_self_test(self):
        r = subprocess.run([sys.executable, str(HOOK), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("pages", r.stdout)
        r = subprocess.run([sys.executable, str(HOOK), "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("FAIL", r.stdout)


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
    unittest.main()
