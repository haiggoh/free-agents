#!/usr/bin/env python3
"""hooks/offload-nudge.py: a PAID session is told free offloading is the default (fan-out included)
and to announce the model; a FREE session keeps the local-offload nudge.

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

    def test_free_session_keeps_the_local_nudge(self):
        text = nudge("http://127.0.0.1:4143")
        self.assertNotIn("FREE OFFLOADING IS THE DEFAULT", text)
        self.assertIn("local:<role>", text)


if __name__ == "__main__":
    unittest.main()
