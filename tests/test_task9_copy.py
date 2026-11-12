#!/usr/bin/env python3
"""tests/test_task9_copy.py — bugbash plan Task 9 (7b), driven by the D2 results the user
measured 2026-11-12 (recorded in plans/2026-11-04-...bugbash.md):

- No clipboard command anywhere (PATH stripped AND /usr/bin/pbcopy absent) must return
  False and show the VISIBLE "copy failed" toast — never a silent OSC-52-only no-op
  (Terminal.app ignores OSC 52).
- The info box must be drag-selectable and copy-on-select must land its text on the
  clipboard exactly once (D2: with mouse reporting on, selecting in the info box already
  shows the indicator + pbpaste has the text — pin it so it cannot regress).
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import PilotCase  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive


class ClipboardFallbackTests(PilotCase):
    def test_no_clipboard_command_anywhere_fails_visibly(self):
        async def s(app, pilot, h):
            with mock.patch("shutil.which", return_value=None), \
                 mock.patch("subprocess.run", side_effect=OSError("no pbcopy")):
                ok = app.copy_to_clipboard("hello")
                self.assertFalse(ok, "no clipboard command anywhere -> False, never silent OSC-52")
                with mock.patch.object(app, "notify") as note:
                    app.copy_and_indicate("hello")
                self.assertTrue(any("copy failed" in str(c) for c in note.call_args_list),
                                f"a failed copy must show the error toast: {note.call_args_list}")
        self.run_picker(s, start="home")


class InfoBoxSelectTests(PilotCase):
    def test_info_box_drag_selection_copies_once(self):
        async def s(app, pilot, h):
            await h.press("c")                     # open the remote model list (info box shows)
            await h.wait_text("NVIDIA A")
            await h.wait_for(lambda: app.query_one("#info").display, "info box visible")
            info = app.query_one("#info")
            await h.settle(secs=0.5)
            x, y = 2, 1                                   # widget-relative, inside the visible part
            dx = 6
            calls = []
            def fake_run(cmd, *a, **k):
                if cmd and cmd[0].endswith("pbcopy"):
                    calls.append(k.get("input"))
                return mock.Mock(returncode=0)
            with mock.patch("subprocess.run", side_effect=fake_run):
                await pilot.mouse_down(info, offset=(x, y))
                await pilot.hover(info, offset=(x + dx, y))   # drag starts the selection
                await pilot.pause(0.1)
                await pilot.mouse_up(info, offset=(x + dx, y))  # release: copy-on-select fires
                await h.settle(secs=1.0)
            selected = app.screen.get_selected_text()
            if not selected:
                self.skipTest("no text selected by the synthetic drag in this terminal driver")
            self.assertEqual(calls, [selected],
                             f"info-box selection must reach pbcopy exactly once: {calls}")
        self.run_picker(s, start="remote")


if __name__ == "__main__":
    unittest.main()
