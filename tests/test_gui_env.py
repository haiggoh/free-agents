#!/usr/bin/env python3
"""GUI environment contract (NiceGUI frontend, plan Task 1).

The GUI runs from its OWN managed venv (install/setup-gui.sh, default
~/.local/share/free-agents/gui-venv, override LA_GUI_VENV). The TUI and direct CLI must keep
working with no GUI package installed, so the shared model modules may never import one.

Usage: python3 tests/test_gui_env.py [-v]
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "bin/free-agents-gui"
SETUP = ROOT / "install/setup-gui.sh"
MAIN = ROOT / "bin/fa_gui/main.py"


def _run(cmd, env_extra=None, timeout=30):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


class WrapperTests(unittest.TestCase):
    def test_wrapper_help_exits_0(self):
        r = _run(["bash", str(WRAPPER), "--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Usage", r.stdout)
        self.assertIn("LA_GUI_VENV", r.stdout)

    def test_wrapper_missing_venv_exit_3_names_setup_gui(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(["bash", str(WRAPPER), "--selftest"], {"LA_GUI_VENV": tmp})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("setup-gui.sh", r.stderr)

    def test_wrapper_invalid_venv_override_aborts_naming_it(self):
        r = _run(["bash", str(WRAPPER), "--selftest"], {"LA_GUI_VENV": "/nonexistent/fa-gui-venv"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("LA_GUI_VENV", r.stderr)

    def test_wrapper_unknown_flag_non_zero(self):
        r = _run(["bash", str(WRAPPER), "--bogus"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Usage", r.stderr)


class SetupTests(unittest.TestCase):
    def test_setup_gui_help(self):
        r = _run(["bash", str(SETUP), "--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Usage", r.stdout)

    def test_setup_gui_check_reports_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(["bash", str(SETUP), "--check"], {"LA_GUI_VENV": str(Path(tmp) / "none")})
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing", r.stderr)

    def test_setup_gui_unknown_flag(self):
        r = _run(["bash", str(SETUP), "--bogus"])
        self.assertEqual(r.returncode, 2)


class MainArgTests(unittest.TestCase):
    def test_main_rejects_unknown_flag(self):
        # argparse runs before any GUI import, so this works in the system interpreter.
        r = _run([sys.executable, str(MAIN), "--bogus"])
        self.assertEqual(r.returncode, 2)
        # argparse's own refusal, not "can't open file" (which also exits 2)
        self.assertIn("unrecognized arguments: --bogus", r.stderr)

    def test_main_help_without_gui_packages(self):
        r = _run([sys.executable, str(MAIN), "--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--browser", r.stdout)


class CoreIsolationTests(unittest.TestCase):
    def test_core_imports_without_gui_packages(self):
        code = ("import sys; sys.path.insert(0, 'bin'); import session_picker_model; "
                "bad = [m for m in ('nicegui', 'webview', 'textual', 'objc') if m in sys.modules]; "
                "print(bad); sys.exit(1 if bad else 0)")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           cwd=ROOT, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
