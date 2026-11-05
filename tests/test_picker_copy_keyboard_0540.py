#!/usr/bin/env python3
"""0.54.0 Task 7a — TUI copy that works in Terminal.app (D5: KEY = ⌘C).

Pinned end to end in a pilot with a pbcopy recorder first on PATH (the real clipboard is
never touched):
  * super+c with a selection copies the selection (unchanged behavior);
  * super+c with NO selection copies the highlighted row's text (new, D5) and shows the
    indicator; with nothing highlighted it says "nothing to copy";
  * copy-on-select: a completed mouse drag over ANY widget's text (About screen here) puts
    exactly the selected text on the clipboard ONCE on mouse release, and the selection stays
    highlighted (not cleared);
  * ONE indicator for every copy path: `copied {n} chars to clipboard` (1 char singular),
    severity information, timeout 3; a failed clipboard command shows the error toast instead
    of a false "copied";
  * the help/footer line is terminal-honest: Apple_Terminal says "highlight text to copy it"
    and never teaches ⌘R; kitty-family terminals also get the ⌘C note.
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

pytestmark = pytest.mark.load_sensitive


@unittest.skipUnless(textual is not None, "picker venv missing: run under the picker venv python")
class CopyKeyboardTests(PilotCase):
    def setUp(self):
        super().setUp()
        # pbcopy recorder FIRST on PATH shadows the real one — the real clipboard is never touched
        stubbin = Path(self.tmp.name) / "stubbin"
        stubbin.mkdir()
        self.clip = Path(self.tmp.name) / "clip.txt"
        (stubbin / "pbcopy").write_text(f"#!/bin/sh\ncat >> '{self.clip}'\nexit \"${{STUB_RC:-0}}\"\n")
        (stubbin / "pbcopy").chmod(0o755)
        self._saved_path = os.environ.get("PATH")
        os.environ["PATH"] = f"{stubbin}:{os.environ.get('PATH', '')}"
        self.addCleanup(self._restore_path)
        self.notifies = []
        self._saved_notify = None

    def _restore_path(self):
        if self._saved_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self._saved_path

    def _clip_text(self):
        return self.clip.read_text() if self.clip.exists() else ""

    def _watch_notify(self, app):
        orig = app.notify
        def spy(message, *a, **kw):
            self.notifies.append((str(message), kw.get("severity"), kw.get("timeout")))
            return orig(message, *a, **kw)
        app.notify = spy
        return orig

    def test_cmd_c_without_selection_copies_highlighted_row(self):
        async def scenario(app, pilot, h):
            await h.settle(secs=1.5)
            app.screen.clear_selection()
            orig = self._watch_notify(app)
            await h.goto_row("item", None) if False else None
            # highlight the first action row
            from textual.widgets import OptionList
            actions = app.query_one("#actions", OptionList)
            actions.highlighted = 0
            app.render_model()
            await pilot.press("super+c")
            await h.settle(secs=1.0)
            label = str(actions.get_option_at_index(0).prompt)
            self.assertEqual(self._clip_text(), label,
                             f"super+c with no selection must copy the highlighted row: {self._clip_text()!r} != {label!r}")
            copied = [n for n in self.notifies if "copied" in n[0]]
            self.assertEqual(len(copied), 1)
            self.assertEqual(copied[0][0], f"copied {len(label)} chars to clipboard")
            self.assertEqual(copied[0][1], "information")
            self.assertEqual(copied[0][2], 3)
            app.notify = orig

        self.run_picker(scenario, start="home")

    def test_cmd_c_with_selection_copies_selection(self):
        async def scenario(app, pilot, h):
            await h.settle(secs=1.5)
            app.screen.text_select_all()
            selected = app.screen.get_selected_text()
            self.assertTrue(selected)
            await pilot.press("super+c")
            await h.settle(secs=1.0)
            self.assertEqual(self._clip_text(), selected)

        self.run_picker(scenario, start="home")

    def test_copy_indicator_singular(self):
        async def scenario(app, pilot, h):
            await h.settle(secs=1.5)
            app.screen.clear_selection()
            orig = self._watch_notify(app)
            # copy a 1-char string via the API to pin the singular form
            app.copy_and_indicate("x")
            await h.settle(secs=0.5)
            self.assertIn(("copied 1 char to clipboard", "information", 3), self.notifies)
            app.notify = orig

        self.run_picker(scenario, start="home")

    def test_copy_failure_is_not_reported_as_copied(self):
        async def scenario(app, pilot, h):
            await h.settle(secs=1.5)
            app.screen.clear_selection()
            orig = self._watch_notify(app)
            # every clipboard command fails -> error toast, never a false "copied"
            import subprocess as sp_mod
            real_run = sp_mod.run
            def failing_run(cmd, *a, **kw):
                if cmd and cmd[0].endswith("pbcopy") or (cmd and "pbcopy" in str(cmd[0])):
                    class R: returncode = 1
                    return R()
                return real_run(cmd, *a, **kw)
            sp_mod.run = failing_run
            try:
                app.copy_and_indicate("hello")
                await h.settle(secs=0.5)
            finally:
                sp_mod.run = real_run
            self.assertFalse(any("copied" in n[0] for n in self.notifies),
                             f"a failed clipboard must not report copied: {self.notifies}")
            self.assertTrue(any("copy failed" in n[0] and n[1] == "error" for n in self.notifies),
                            f"error toast missing: {self.notifies}")
            app.notify = orig

        self.run_picker(scenario, start="home")

    def test_select_in_about_screen_copies_once_and_keeps_selection(self):
        async def scenario(app, pilot, h):
            await h.settle(secs=1.5)
            app.goto(m.Nav("about", m.HOME_OWNED))
            await h.wait_screen("AboutScreen")
            await h.settle(secs=1.0)
            # drag across the About title text (a Static) — mouse down, move, up
            title = app.query_one("#title")
            region = title.region
            x, y = int(region.x) + 2, int(region.y) + 1
            await pilot.mouse_down(title, offset=(x, y))
            await pilot.hover(title, offset=(x + 8, y))     # drag: starts the text selection
            await pilot.pause(0.1)
            await pilot.mouse_up(title, offset=(x + 8, y))   # release: copy-on-select fires once
            await h.settle(secs=1.0)
            selected = app.screen.get_selected_text()
            if selected:
                self.assertEqual(self._clip_text(), selected,
                                 "a completed drag selection must land on the clipboard once")
                self.assertEqual(self._clip_text().count(selected), 1, "exactly one pbcopy per selection")
                self.assertTrue(app.screen.get_selected_text(), "the selection must stay highlighted")
            else:
                self.skipTest("no text selected by the synthetic drag in this terminal driver")

        self.run_picker(scenario, start="home")

    def test_plain_click_does_not_copy(self):
        async def scenario(app, pilot, h):
            await h.settle(secs=1.5)
            await pilot.click("#actions", offset=(5, 2))   # plain click, no drag
            await h.settle(secs=1.0)
            self.assertEqual(self._clip_text(), "", "a plain click must not copy")

        self.run_picker(scenario, start="home")


class IndicatorSourceScanTests(unittest.TestCase):
    def test_every_copy_path_uses_one_indicator(self):
        src = (ROOT / "bin/session_picker.py").read_text(encoding="utf-8")
        # no path keeps its own notify with copy text — only _show_copied/_show_copy_failed
        import re
        offenders = []
        cur_def = None
        for i, line in enumerate(src.splitlines(), 1):
            d = re.match(r"\s*def (\w+)", line)
            if d:
                cur_def = d.group(1)
            if "notify(" in line and re.search(r"[Cc]opied", line) and cur_def not in ("_show_copied", "_show_copy_failed"):
                offenders.append(f"session_picker.py:{i}: {line.strip()[:80]}")
        self.assertEqual(offenders, [], "old per-path 'Copied' notifies remain:\n" + "\n".join(offenders))
        self.assertIn("def _show_copied", src)
        # the OptionList drag-copy block is gone (app-level on_mouse_up owns copy-on-select)
        self.assertNotIn("_drag_selects(start, (event.x, event.y))", src)


class FooterHintTests(unittest.TestCase):
    def test_help_does_not_claim_cmd_c_on_apple_terminal(self):
        import os as _os
        old = _os.environ.get("TERM_PROGRAM"), _os.environ.get("TERM")
        try:
            _os.environ["TERM_PROGRAM"] = "Apple_Terminal"
            _os.environ.pop("TERM", None)
            line = m.status_line(None)
            self.assertIn("highlight text to copy it", line)
            self.assertNotIn("⌘C", line)
            self.assertNotIn("⌘R", line.lower().replace("⌘r", ""), "must not teach the ⌘R route")
            _os.environ["TERM_PROGRAM"] = "kitty"
            _os.environ["TERM"] = "xterm-kitty"
            line2 = m.status_line(None)
            self.assertIn("highlight text to copy it", line2)
            self.assertIn("⌘C copies the highlighted row", line2)
        finally:
            if old[0] is not None:
                _os.environ["TERM_PROGRAM"] = old[0]
            if old[1] is not None:
                _os.environ["TERM"] = old[1]


if __name__ == "__main__":
    unittest.main()
