#!/usr/bin/env python3
"""tests/test_picker_copy_0508.py — 0.50.8 Task 4: copy must work under csl too.

User 2026-11-02: "the copy function is still blocked in CSL". Two choke points fixed:
  1. the picker's copy_to_clipboard now also tries the ABSOLUTE /usr/bin/pbcopy — a
     csl-launched child can run with a stripped PATH where `which pbcopy` finds nothing,
     and OSC 52 alone is ignored by Terminal.app, so copy silently did nothing;
  2. the csl-owned shell menu (csl remote) got an explicit `y` copy action (covered by
     tests/test_csl_menu.sh section 11).

Pinned here end to end in a pilot: with a selection active, Ctrl+C lands the selected text
on the (stubbed) clipboard and the app KEEPS RUNNING (copy_or_quit must not quit when there
is a selection). Mutation anchor: removing the ctrl+c binding -> the app quits on ctrl+c /
no clipboard write -> red.

Usage: python3 tests/test_picker_copy_0508.py -v   (or pytest)
"""
import os
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import session_picker_model as m  # noqa: E402
from picker_harness import PilotCase  # noqa: E402

try:
    import textual  # noqa: F401
except ImportError:
    textual = None

pytestmark = pytest.mark.load_sensitive  # drives the real picker in a pilot process


@unittest.skipUnless(textual is not None, "picker venv missing: run under the picker venv python")
class PickerCopy(PilotCase):
    def setUp(self):
        super().setUp()
        # A stub pbcopy FIRST on PATH records what the app hands to the system clipboard;
        # it must shadow the real /usr/bin/pbcopy so the test never touches the real clipboard.
        stubbin = Path(self.tmp.name) / "stubbin"
        stubbin.mkdir()
        self.clip = Path(self.tmp.name) / "clip.txt"
        (stubbin / "pbcopy").write_text(f"#!/bin/sh\ncat > '{self.clip}'\n")
        (stubbin / "pbcopy").chmod(0o755)
        self._saved_path = os.environ.get("PATH")
        os.environ["PATH"] = f"{stubbin}:{os.environ.get('PATH', '')}"
        self.addCleanup(self._restore_path)

    def _restore_path(self):
        if self._saved_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self._saved_path

    def test_ctrl_c_copies_selection_and_does_not_quit(self):
        async def scenario(app, pilot, h):
            await h.settle(secs=1.5)
            scr = app.screen
            scr.text_select_all()                      # a selection exists (drag path tested by hand)
            selected = scr.get_selected_text()
            self.assertTrue(selected, "precondition: a selection must exist for this test")
            await pilot.press("ctrl+c")
            await h.settle(secs=1.0)
            self.assertTrue(self.clip.exists() and self.clip.read_text() == selected,
                            f"ctrl+c must land the selection on the clipboard (got {self.clip.read_text() if self.clip.exists() else '<none>'!r})")
            self.assertTrue(app.is_running, "ctrl+c WITH a selection must copy, not quit")

        self.run_picker(scenario, start="home")

    def test_ctrl_c_without_selection_quits(self):
        async def scenario(app, pilot, h):
            await h.settle(secs=1.5)
            app.screen.clear_selection()
            await pilot.press("ctrl+c")
            await h.settle(secs=1.0)
            self.assertFalse(self.clip.exists() and self.clip.read_text(),
                             "no selection -> nothing copied")
            self.assertFalse(app.is_running, "ctrl+c WITHOUT a selection quits (copy_or_quit)")

        self.run_picker(scenario, start="home")


if __name__ == "__main__":
    unittest.main()
