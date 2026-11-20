#!/usr/bin/env python3
"""Task 8 wiring: the launchers accept --priority (exported into the proxy env via
bin/rl-lane-env.sh) and the dispatch entry points mark themselves LA_LIMITER_KIND=dispatch,
so dispatches/background sessions rank ahead of interactive within tier 0 only."""
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LaneEnvScriptTests(unittest.TestCase):
    def test_help_runs_and_documents(self):
        r = subprocess.run(["bash", str(BIN / "rl-lane-env.sh"), "--help"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--priority", r.stdout)
        self.assertIn("--kind", r.stdout)

    def test_priority_prints_export(self):
        r = subprocess.run(["bash", str(BIN / "rl-lane-env.sh"), "--priority"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("export LA_LIMITER_PRIORITY=1", r.stdout)

    def test_kind_dispatch(self):
        r = subprocess.run(["bash", str(BIN / "rl-lane-env.sh"), "--kind", "dispatch"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("export LA_LIMITER_KIND=dispatch", r.stdout)

    def test_unknown_flag_fails_loud(self):
        r = subprocess.run(["bash", str(BIN / "rl-lane-env.sh"), "--bogus"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown flag", r.stderr)

    def test_export_actually_lands_in_a_shell(self):
        # delivery-level: eval the helper's output in a child shell, read the env back
        r = subprocess.run(
            ["bash", "-c", 'eval "$("$1" --priority --kind dispatch)"; '
                           'printf "%s|%s" "$LA_LIMITER_PRIORITY" "$LA_LIMITER_KIND"',
             "bash", str(BIN / "rl-lane-env.sh")],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "1|dispatch")


class LauncherFlagTests(unittest.TestCase):
    def test_launchers_parse_syntax(self):
        for name in ("remote-session.sh", "launch-claude-agent.sh"):
            with self.subTest(launcher=name):
                r = subprocess.run(["bash", "-n", str(BIN / name)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_remote_session_help_lists_priority_and_the_flag_exports(self):
        # --help run: rc 0 and documents the flag; xtrace of the same invocation proves the
        # parse arm actually evals the export (no real session is started — --help exits)
        r = subprocess.run(["bash", str(BIN / "remote-session.sh"), "--priority", "--help"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[:300])
        self.assertIn("--priority", r.stdout)
        t = subprocess.run(["bash", "-x", str(BIN / "remote-session.sh"), "--priority", "--help"],
                           capture_output=True, text=True)
        import re
        self.assertRegex(t.stderr, r"\+\s*eval .*LA_LIMITER_PRIORITY=1",
                         "the --priority arm must eval the export line (not just print it in usage)")

    def test_launch_claude_agent_help_lists_priority(self):
        r = subprocess.run(["bash", str(BIN / "launch-claude-agent.sh"), "--help"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[:300])
        self.assertIn("--priority", r.stdout)


class DispatchKindTests(unittest.TestCase):
    def test_dispatch_tools_set_kind_through_main(self):
        # delivery-level: run each tool's REAL main() (--help path) in a child process and
        # read the env back from stderr — proves main() calls _limiter_kind_env(), not just
        # that the function exists
        for name, call in (
                ("free-agent-tool.py", "sys.argv = ['x', '--help']; m.main()"),
                ("remote-agent-dispatch.py",
                 "try:\n    m.main(['--help'])\nexcept SystemExit:\n    pass")):
            with self.subTest(tool=name):
                code = ("import sys, os, importlib.util\n"
                        f"spec = importlib.util.spec_from_file_location('m', {str(BIN / name)!r})\n"
                        "m = importlib.util.module_from_spec(spec)\n"
                        "spec.loader.exec_module(m)\n"
                        + call + "\n"
                        "sys.stderr.write('KIND=' + os.environ.get('LA_LIMITER_KIND', ''))\n")
                r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-300:])
                self.assertIn("KIND=dispatch", r.stderr)

    def test_dispatch_tools_help_runs(self):
        for name in ("free-agent-tool.py", "remote-agent-dispatch.py"):
            with self.subTest(tool=name):
                r = subprocess.run([sys.executable, str(BIN / name), "--help"],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{name} --help rc={r.returncode}: {r.stderr[:200]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
