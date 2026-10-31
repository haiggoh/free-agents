#!/usr/bin/env python3
"""tests/test_last_effort_0493.py — 0.49.3: an omitted --effort defaults to the alias's stored
last-launch effort (never silently to medium when the user's stored level is higher).

Incident 2026-10-31: a handover launched without --effort ran at medium instead of the user's
stored max. remote-session.sh now resolves an omitted --effort via bin/last-effort.py and records
the effective effort on every real launch. Each test names what breaks it."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "bin"))
from test_remote_session import RemoteSessionTests  # noqa: E402  (reuse the stubbed harness)


class _Harness(RemoteSessionTests):
    __test__ = False  # pytest: do not collect the inherited parent tests, only mine below


class LastEffortFileTests(unittest.TestCase):
    def run_tool(self, *args, env_extra=None):
        env = dict(os.environ)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, str(ROOT / "bin/last-effort.py"), *args],
                              env=env, capture_output=True, text=True)

    def test_help_exits_zero_and_documents_env(self):
        r = self.run_tool("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("LA_LAST_EFFORT_FILE", r.stdout)
        self.assertIn("get", r.stdout)

    def test_set_get_roundtrip_and_invalid_level_fails_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "s.json"
            env = {"LA_LAST_EFFORT_FILE": str(f)}
            r = self.run_tool("set", "a1", "max", env_extra=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self.run_tool("get", "a1", env_extra=env)
            self.assertEqual(r.stdout.strip(), "max")
            r = self.run_tool("set", "a1", "bogus", env_extra=env)
            self.assertNotEqual(r.returncode, 0, "an invalid level must abort, not fall through")

    def test_corrupt_state_fails_open(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "s.json"
            f.write_text("{not json")
            r = self.run_tool("get", "a1", env_extra={"LA_LAST_EFFORT_FILE": str(f)})
            self.assertEqual(r.returncode, 0, "corrupt state must not break a launch (fail-open)")
            self.assertEqual(r.stdout.strip(), "")


class EffortDefaultLaunchTests(_Harness):
    __test__ = True

    def setUp(self):
        super().setUp()
        # the fixture root must carry the new store script, like the real repo does
        shutil.copy2(ROOT / "bin/last-effort.py", self.root / "bin/last-effort.py")

    def run_env(self, *args, extra=None):
        env = dict(self.env, CLAUDE_ARGV=str(self.root / "claude-argv"))
        env.update(extra or {})
        return subprocess.run(["bash", str(self.root / "bin/remote-session.sh"), *args],
                              env=env, text=True, capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=120)

    def state_file(self, data=None):
        f = self.root / "last-effort.json"
        if data is not None:
            f.write_text(json.dumps(data))
        return f

    def test_dry_run_uses_stored_effort_when_flag_omitted(self):
        sf = self.state_file({"nvidia-mistral-large4": "max"})
        r = self.run_env("--dry-run", "nvidia-mistral-large4",
                         extra={"LA_LAST_EFFORT_FILE": str(sf)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("effort         : max", r.stdout,
                      "omitted --effort must resolve to the stored last-launch effort")

    def test_explicit_flag_wins_over_stored(self):
        sf = self.state_file({"nvidia-mistral-large4": "max"})
        r = self.run_env("--dry-run", "nvidia-mistral-large4", "--effort", "high",
                         extra={"LA_LAST_EFFORT_FILE": str(sf)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("effort         : high", r.stdout)

    def test_no_state_means_provider_default(self):
        # planted negative: without the store the old behaviour (provider default) must show,
        # proving the dry-run tests above are driven by the state file, not a hardcoded copy
        sf = self.state_file()  # file absent
        r = self.run_env("--dry-run", "nvidia-mistral-large4",
                         extra={"LA_LAST_EFFORT_FILE": str(sf)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("effort         : <provider default>", r.stdout)

    def test_real_launch_records_effort_and_next_dry_run_uses_it(self):
        sf = self.state_file()  # empty
        extra = {"LA_LAST_EFFORT_FILE": str(sf)}
        r = self.run_env("nvidia-mistral-large4", "--effort", "max", extra=extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(sf.read_text())
        self.assertEqual(data.get("nvidia-mistral-large4"), "max",
                         "a real launch must record the effective effort")
        argv = json.loads((self.root / "claude-argv").read_text())
        self.assertIn("max", argv, "claude itself must be launched with --effort max")
        r2 = self.run_env("--dry-run", "nvidia-mistral-large4", extra=extra)
        self.assertIn("effort         : max", r2.stdout,
                      "the next launch without --effort must pick up the stored max")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
    unittest.main()
