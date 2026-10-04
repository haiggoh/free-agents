#!/usr/bin/env python3
"""tests/test_lowkey_effort.py — `lowkey --effort` must change the REQUEST, not a label.

Runs the real bin/lowkey-cli.py in a temp tree whose local-llm-hotswap.sh and
librarian-dispatch.py are stubs: the hotswap stub reports a port without loading any
weights, and the librarian stub records the exact chat-completions body it was handed.
So each assertion is about the bytes that would reach Rapid-MLX.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HOTSWAP = """#!/bin/sh
echo "SUCCESS_PORT=65000"
echo "DISPATCH_MODEL=fixture-served-id"
"""

LIBRARIAN = """#!/usr/bin/env python3
import json, os, sys
a = sys.argv
body = json.load(open(a[a.index('--payload') + 1]))
with open(os.environ['LK_CAPTURE'], 'a') as fh:
    fh.write(json.dumps(body) + '\\n')
open(os.path.join(a[a.index('--outdir') + 1], 'output.txt'), 'w').write('fixture reply')
"""


class LowkeyEffortTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "bin").mkdir()
        shutil.copy2(ROOT / "bin/lowkey-cli.py", root / "bin/lowkey-cli.py")
        shutil.copy2(ROOT / "VERSION", root / "VERSION")
        for name, body in (("local-llm-hotswap.sh", HOTSWAP), ("librarian-dispatch.py", LIBRARIAN)):
            (root / "bin" / name).write_text(body)
            (root / "bin" / name).chmod(0o755)
        self.cli = root / "bin/lowkey-cli.py"
        self.capture = root / "capture.jsonl"
        self.env = dict(os.environ, LK_CAPTURE=str(self.capture),
                        LOCAL_AGENT_SESSION_DIR=str(root / "sessions"))

    def run_cli(self, *args, stdin=""):
        return subprocess.run([sys.executable, str(self.cli), "--progress", "quiet", *args],
                              input=stdin, env=self.env, text=True, capture_output=True, timeout=60)

    def bodies(self):
        if not self.capture.exists():
            return []
        return [json.loads(line) for line in self.capture.read_text().splitlines()]

    def test_one_shot_sends_reasoning_effort(self):
        # Fails if --effort is parsed but never reaches the payload.
        r = self.run_cli("--effort", "low", "--prompt", "hi")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([b.get("reasoning_effort") for b in self.bodies()], ["low"])

    def test_conversation_sends_reasoning_effort_every_turn(self):
        r = self.run_cli("--convo", "--effort", "xhigh", stdin="first\nsecond\nexit\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        efforts = [b.get("reasoning_effort") for b in self.bodies()]
        self.assertEqual(efforts, ["xhigh", "xhigh"], r.stdout[-400:])

    def test_distinct_efforts_produce_distinct_requests(self):
        for value in ("none", "medium"):
            self.run_cli("--effort", value, "--prompt", "hi")
        self.assertEqual([b.get("reasoning_effort") for b in self.bodies()], ["none", "medium"])

    def test_no_flag_sends_no_field(self):
        # Fails if an unrequested default is injected: omission lets the server/template decide.
        r = self.run_cli("--prompt", "hi")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("reasoning_effort", self.bodies()[0])

    def test_values_the_server_rejects_are_refused_before_dispatch(self):
        # Rapid-MLX answers HTTP 400 to `max`; refusing locally beats a failed turn.
        r = self.run_cli("--effort", "max", "--prompt", "hi")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.bodies(), [])

    def test_dry_run_reports_effort_and_dispatches_nothing(self):
        r = self.run_cli("--dry-run", "--effort", "high", "--prompt", "hi")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Effort         : high", r.stdout)
        self.assertEqual(self.bodies(), [])


if __name__ == "__main__":
    unittest.main()
