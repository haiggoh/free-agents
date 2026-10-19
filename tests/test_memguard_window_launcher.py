#!/usr/bin/env python3
"""tests/test_memguard_window_launcher.py — pin the launcher's Memory-guard window gating.

What this protects: LA_MEMGUARD_WINDOW is the only thing between a local session and a mis-sized
auto-compact window, and a regression that inverts the gate, drops the export, or breaks the
module call would leave the guard silently off (or on) with nothing red — the workflow review found
this seam had zero automated coverage. Both states are pinned: OFF by default (no compute, no
export), and ON computing + exporting a window through the launcher's own dry-run.

Machine-dependent by design (real roster alias + manifest), like test_launcher_smoke.py; the
tests skip cleanly when the fixture alias is not launchable on this machine.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "bin" / "launch-claude-agent.sh"
ALIAS = "qwen-3.8-operator"


def _dry_run(env_extra):
    env = dict(os.environ, **env_extra)
    return subprocess.run(["bash", str(LAUNCHER), "--dry-run-skip-preflight", ALIAS, "high"],
                          capture_output=True, text=True, env=env, timeout=180)


class WindowLauncherTests(unittest.TestCase):
    def test_window_off_by_default(self):
        # Fails if the window computes or exports with the switch unset (default changes nothing).
        r = _dry_run({})
        if "Memory guard" not in r.stdout:          # alias not launchable on this machine
            self.skipTest(f"{ALIAS} not launchable here: {(r.stdout + r.stderr)[-200:]!r}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("window off (LA_MEMGUARD_WINDOW=0)", r.stdout)
        self.assertNotIn("RAM-calibrated auto-compact window", r.stdout)

    def test_window_on_computes_and_exports(self):
        # Fails if the ON path stops computing a window or the existing validator stops it.
        with tempfile.TemporaryDirectory() as tmp:
            store = dict(os.environ, FREE_AGENTS_CONFIG_DIR=tmp)
            subprocess.run([sys.executable, str(ROOT / "bin/session_menu_state.py"),
                            "kv-set", ALIAS, "int8"], env=store, capture_output=True)
            r = _dry_run({"LA_MEMGUARD_WINDOW": "1", "FREE_AGENTS_CONFIG_DIR": tmp})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("RAM-calibrated auto-compact window = ", r.stdout)
            self.assertRegex(r.stdout, r"Autocompact   : [0-9]+")
            self.assertIn("window ON", r.stdout)


if __name__ == "__main__":
    unittest.main()
