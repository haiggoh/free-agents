#!/usr/bin/env python3
"""fa_fallback_mark: the session-scoped, single-use, expiring "free exhausted" mark.

The cloud-agent-intercept hook consumes exactly one mark to let one native paid call through
after honest exhaustion. Marks are keyed by session id (review focus 1: a mark from one cloud
session must never unlock another) and expire (plan default 900 s)."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import fa_fallback_mark as fm  # noqa: E402


class FallbackMark(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old = os.environ.get("FA_FALLBACK_MARK_DIR")
        os.environ["FA_FALLBACK_MARK_DIR"] = self.tmp.name
        self.addCleanup(lambda: (os.environ.pop("FA_FALLBACK_MARK_DIR", None) if self._old is None
                                 else os.environ.__setitem__("FA_FALLBACK_MARK_DIR", self._old)))

    def test_write_then_consume_returns_and_deletes(self):
        fm.write_mark("sess-a", [{"id": "r1", "ok": False}])
        doc = fm.consume_mark("sess-a")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["session_id"], "sess-a")
        self.assertFalse(fm.mark_path("sess-a").exists(), "consumed marks are gone (single use)")
        self.assertIsNone(fm.consume_mark("sess-a"), "a second consume finds nothing")

    def test_session_as_mark_is_not_consumed_by_another_session(self):
        fm.write_mark("sess-a", [])
        self.assertIsNone(fm.consume_mark("sess-b"), "session B's Agent call stays redirected")
        self.assertIsNotNone(fm.consume_mark("sess-a"), "session A still owns its mark")

    def test_expired_mark_is_ignored_and_removed(self):
        fm.write_mark("sess-a", [], ttl_s=10)
        doc = fm.consume_mark("sess-a", now=time.time() + 11)
        self.assertIsNone(doc, "expired marks never arm the paid path")
        self.assertFalse(fm.mark_path("sess-a").exists(), "expired marks are cleaned up")

    def test_mark_file_is_mode_600(self):
        p = fm.write_mark("sess-a", [])
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
