#!/usr/bin/env python3
"""tests/test_gui_main_args.py — Phase 6 regression guard: native window args must go on
app.native.window_args, NEVER as a ui.run kwarg — NiceGUI 3.19's ui.run forwards unknown
kwargs to CustomServerConfig, which raises TypeError (measured: real --native launch died
2026-11-08 with "unexpected keyword argument 'window_args'").

Run under the GUI venv: gui-venv/bin/python -m pytest -q tests/test_gui_main_args.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def _load_main():
    spec = importlib.util.spec_from_file_location("fa_gui_main", ROOT / "bin" / "fa_gui" / "main.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class NativeWindowArgsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.appearance = Path(self.tmp.name) / "gui-appearance.json"
        self._old_env = os.environ.get("LA_GUI_APPEARANCE_FILE")
        os.environ["LA_GUI_APPEARANCE_FILE"] = str(self.appearance)
        self.addCleanup(self._restore_env)
        import nicegui
        from nicegui import ui
        self.nicegui, self.ui = nicegui, ui
        self._real_run = ui.run
        self.calls = []
        ui.run = lambda **kw: self.calls.append(kw) or 0
        self.addCleanup(self._restore_run)
        self.main = _load_main()

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop("LA_GUI_APPEARANCE_FILE", None)
        else:
            os.environ["LA_GUI_APPEARANCE_FILE"] = self._old_env

    def _restore_run(self):
        self.ui.run = self._real_run

    def test_native_mode_sets_app_native_window_args_not_ui_run_kwarg(self):
        self.appearance.write_text(json.dumps(
            {"schema_version": 1, "background_translucent": True, "background_blur_requested": True}))
        self.nicegui.app.native.window_args.clear()
        rc = self.main.main(["--native"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls), 1)
        kw = self.calls[0]
        self.assertTrue(kw.get("native"))
        self.assertNotIn("window_args", kw,
                         "ui.run must NOT take window_args — NiceGUI 3.19 crashes on it")
        self.assertEqual(self.nicegui.app.native.window_args,
                         {"transparent": True, "vibrancy": True},
                         "args land on app.native.window_args, which native_mode merges")

    def test_browser_mode_never_touches_window_args(self):
        self.appearance.write_text(json.dumps(
            {"schema_version": 1, "background_translucent": True, "background_blur_requested": True}))
        self.nicegui.app.native.window_args.clear()
        rc = self.main.main(["--browser", "--port", "0"])
        self.assertEqual(rc, 0)
        kw = self.calls[0]
        self.assertNotIn("window_args", kw)
        self.assertNotIn("native", kw)
        self.assertEqual(self.nicegui.app.native.window_args, {},
                         "browser mode must never imply desktop window args")

    def test_default_store_means_no_window_args(self):
        # no file -> defaults -> opaque window, empty args
        rc = self.main.main(["--native"])
        self.assertEqual(rc, 0)
        self.assertNotIn("window_args", self.calls[0])
        self.assertEqual(self.nicegui.app.native.window_args, {})


if __name__ == "__main__":
    unittest.main()
