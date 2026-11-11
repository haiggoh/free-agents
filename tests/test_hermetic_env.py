#!/usr/bin/env python3
"""tests/test_hermetic_env.py — L2 structural tests for the hermetic env helpers.

1. No bare `env -i` survives in tests/ outside tests/lib/hermetic_env.sh — the 19 old
   hand-rolled sites all went through the helper (a new bare one fails this test).
2. The helpers pass through the conftest fence vars (real limiter/HF cache/quota/notify
   state stay per-session tmp files, never the machine-wide ones).
3. Functional: a hermetic child sees the fence value and does NOT see a random parent var.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "lib"))
sys.path.insert(0, str(ROOT / "bin"))

from hermetic_env import FENCE_VARS, hermetic_env  # noqa: E402


class NoBareEnvITI(unittest.TestCase):
    def test_no_bare_env_dash_i_outside_the_helper(self):
        offenders = []
        for f in list((ROOT / "tests").glob("*.sh")) + list((ROOT / "tests").glob("*.py")):
            if f.name == "hermetic_env.sh" and f.parent.name == "lib":
                continue
            if f.name == "test_hermetic_env.py":   # this test's own source mentions the pattern
                continue
            for n, line in enumerate(f.read_text().splitlines(), 1):
                if "env -i" in line and "hermetic_env" not in line:
                    offenders.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
        self.assertEqual(offenders, [], "bare `env -i` bypasses the fence passthrough:\n" + "\n".join(offenders))


class HelperContractTests(unittest.TestCase):
    def test_shell_helper_lists_every_fence_var(self):
        src = (ROOT / "tests/lib/hermetic_env.sh").read_text()
        for v in FENCE_VARS:
            self.assertIn(v, src, f"shell helper must pass through {v}")

    def test_python_helper_lists_every_fence_var(self):
        import hermetic_env as h
        for v in FENCE_VARS:
            self.assertIn(v, h.FENCE_VARS)

    def test_child_sees_fence_var_not_parent_secret(self):
        os.environ["FA_HERMETIC_PROBE_SECRET"] = "leak-me"
        os.environ["LA_NVIDIA_THROTTLE_STATE"] = "/tmp/fence-probe/throttle.json"
        try:
            env = hermetic_env()
            self.assertEqual(env["LA_NVIDIA_THROTTLE_STATE"], "/tmp/fence-probe/throttle.json")
            self.assertNotIn("FA_HERMETIC_PROBE_SECRET", env)
            out = subprocess.run(
                ["sh", "-c", "printenv LA_NVIDIA_THROTTLE_STATE; printenv FA_HERMETIC_PROBE_SECRET; true"],
                env=env, capture_output=True, text=True).stdout.splitlines()
            self.assertIn("/tmp/fence-probe/throttle.json", out)
            self.assertNotIn("leak-me", out)
        finally:
            os.environ.pop("FA_HERMETIC_PROBE_SECRET", None)
            os.environ.pop("LA_NVIDIA_THROTTLE_STATE", None)

    def test_shell_helper_runs_command_and_omits_empty_fence(self):
        os.environ["FA_HERMETIC_PROBE_SECRET"] = "leak-me"
        os.environ.pop("LA_HF_PARAMS_CACHE", None)   # empty must be OMITTED, not passed empty
        try:
            r = subprocess.run(
                ["sh", "-c",
                 f'source "{ROOT}/tests/lib/hermetic_env.sh"; '
                 'hermetic_env printenv LA_HF_PARAMS_CACHE; hermetic_env printenv FA_HERMETIC_PROBE_SECRET; true'],
                env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
                     "FA_HERMETIC_PROBE_SECRET": "leak-me"},
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = [line for line in r.stdout.splitlines() if line]
            self.assertEqual(lines, [], f"child must see NEITHER the empty fence var NOR the parent secret: {lines}")
        finally:
            os.environ.pop("FA_HERMETIC_PROBE_SECRET", None)


if __name__ == "__main__":
    unittest.main()
