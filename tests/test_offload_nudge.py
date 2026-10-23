#!/usr/bin/env python3
"""hooks/offload-nudge.py: a PAID session is told free offloading is the default (fan-out included)
and to announce the model; a FREE session gets the council nudge (no cost reason, but cross-family review still pays).

Usage: python3 tests/test_offload_nudge.py [-v]
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "offload-nudge.py"


def nudge(base_url):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_BASE_URL"}
    if base_url is not None:
        env["ANTHROPIC_BASE_URL"] = base_url
    env["CLAUDE_PLUGIN_ROOT"] = "/plug"
    r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=10)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


class NudgeTests(unittest.TestCase):
    def test_paid_session_makes_free_offloading_the_default(self):
        for base in (None, "https://gateway.example.com"):
            text = nudge(base)
            self.assertIn("FREE OFFLOADING IS THE DEFAULT", text)
            for word in ("Agent", "Workflow", "fan-out", "ANNOUNCE", "/plug/bin/free-agent-tool.py",
                         "STATELESS", "SESSION", "Gemini 3.8 Flash"):
                self.assertIn(word, text, word)

    def test_free_session_gets_the_council_nudge_not_the_cost_one(self):
        # On a free session offloading saves no money, but a cross-family review still adds quality
        # (user 2026-10-23). The old local nudge told free sessions they were "on the paid model".
        for base in ("http://127.0.0.1:4143", "http://localhost:8000", "http://[::1]:4141"):
            text = nudge(base)
            self.assertNotIn("FREE OFFLOADING IS THE DEFAULT", text, base)
            self.assertNotIn("paid model", text, base)
            for word in ("saves no money", "DIFFERENT model family", "cross-check", '"avoid_family"',
                         '"files"', "/plug/bin/free-agent-tool.py", "Announce"):
                self.assertIn(word, text, word)
            self.assertNotIn("}}", text)


if __name__ == "__main__":
    unittest.main()
