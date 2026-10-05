#!/usr/bin/env python3
"""tests/test_session_picker_pty.py — drive the REAL picker against stub launchers.

Navigation, launch argv/effort/cwd, persistence and the downloader run through Textual's pilot
(picker_harness.PilotCase), which reads widget state instead of scraping a terminal Textual only
partially repaints. What only a real terminal can show stays a PTY test: Ctrl-C restoring the
tty modes, the bash entry points, exit codes. Every launcher is a recorder stub, so no model,
proxy or provider is touched. Skips (loudly) when the picker venv is absent.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_session_picker_pty.py
"""
import os
import sys
import termios
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import ROOT, PilotCase, PTYCase  # noqa: E402


class PickerNavigation(PilotCase):
    def test_home_has_quit_not_back_and_q_exits(self):
        async def s(app, pilot, h):
            acts = h.labels("actions")
            self.assertIn("Claude Code Free-Agents: Session Launcher", h.title())
            self.assertTrue(any("Q) Quit" in a for a in acts), acts)
            self.assertFalse(any("Back to" in a for a in acts), acts)
            await h.wait_text("Local sessions (12 on disk)")
            await h.press("q")
            await h.wait_for(lambda: app.return_code is not None, "exit")
            self.assertEqual(app.return_code, 0)
        self.run_picker(s)

    def test_home_owned_local_has_back_no_quit_and_back_returns_home(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            acts = h.labels("actions")
            self.assertTrue(any("Back to Home" in a for a in acts), acts)
            self.assertFalse(any("Quit" in a for a in acts), acts)
            await h.press("b")
            await h.wait_screen("HomeScreen")
        self.run_picker(s)

    def test_direct_root_remote_has_quit_no_back_and_switch_keeps_owner(self):
        async def s(app, pilot, h):
            self.assertEqual(h.screen(), "RemoteScreen")
            acts = h.labels("actions")
            self.assertTrue(any("Quit" in a for a in acts), acts)
            self.assertFalse(any("Back to" in a for a in acts), acts)
            await h.press("s")                       # Switch to Local keeps the direct-root owner
            await h.wait_screen("LocalScreen")
            acts = h.labels("actions")
            self.assertTrue(any("Quit" in a for a in acts), acts)
            self.assertFalse(any("Back to" in a for a in acts), acts)
        self.run_picker(s, start="remote")

    def test_digits_launch_nothing_even_with_twelve_models(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("deepseek-r1-architect")
            await h.press("1", "1", "enter")
            await h.settle(0.8)
            self.assertEqual(self.launches(), [])
        self.run_picker(s, start="local")


class PickerLaunch(PilotCase):
    async def _launch_first_local(self, h):
        await h.press("c")                           # model list is on demand ("Choose model…")
        await h.wait_text("deepseek-r1-architect")
        await h.goto_row("item", "deepseek-r1-architect")
        await h.press("enter")
        await h.wait_for(lambda: self.launches(), "a recorded launch")

    def test_launch_passes_effort_cwd_and_returns_to_same_picker(self):
        async def s(app, pilot, h):
            await self._launch_first_local(h)
            rec = self.launches()[0]
            self.assertEqual(rec["prog"], "csl")
            self.assertEqual(rec["argv"], ["--picker-launch", "deepseek-r1-architect", "high"])
            self.assertEqual(rec["cwd"], str(self.workdir))
            self.assertEqual(rec["env"]["LA_BLIND_AUTO"], "1")
            await h.wait_screen("LocalScreen")       # back on the same picker afterwards
            self.assertEqual(self.saved_state()["last_launched"]["local_session"], "deepseek-r1-architect")
        self.run_picker(s, start="local")

    def test_failed_child_still_returns_to_picker(self):
        os.environ["STUB_RC"] = "7"
        self.addCleanup(os.environ.pop, "STUB_RC", None)

        async def s(app, pilot, h):
            await self._launch_first_local(h)
            await h.wait_text("last child exited 7")
            self.assertEqual(h.screen(), "LocalScreen")
        self.run_picker(s, start="local")

    def test_remote_provider_default_omits_effort_and_is_persisted(self):
        async def s(app, pilot, h):
            await h.wait_text("Effort: max")
            await h.press("e")                       # max -> provider default
            await h.wait_text("Effort: Provider default")
            self.assertEqual(self.saved_state()["effort"]["remote_api_session"], "provider_default")
            await h.press("c")
            await h.wait_text("NVIDIA A")              # rows show the display name; id is the alias
            await h.goto_row("item", "nvidia-a")
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "a recorded launch")
            rec = self.launches()[0]
            self.assertEqual(rec["prog"], "remote-session.sh")
            self.assertNotIn("--effort", rec["argv"])
            self.assertEqual(rec["argv"][-1], "nvidia-a")
        self.run_picker(s, start="remote")

    def test_trials_visible_by_default_and_filter_hides(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("Cerebras (1)")
            await h.press("y")                       # Filter settings… submenu
            await h.wait_screen("RemoteFiltersScreen")
            await h.wait_text("Limited trials: SHOWN")
            await h.press("h")
            await h.wait_text("Limited trials: HIDDEN")
            await h.press("b")                       # a sub-screen: Back returns to Remote
            await h.wait_screen("RemoteScreen")
            if "Choose model…" in h.text():
                await h.press("c")
            await h.wait_text("Gemini")
            self.assertNotIn("Cerebras (1)", h.text())
        self.run_picker(s, start="remote")


class PickerTools(PilotCase):
    def test_downloader_lists_catalog_cancel_zero_calls_confirm_exact_aliases(self):
        async def s(app, pilot, h):
            # Regression (0.22.3-0.23.0): the catalog was never loaded, so this list was empty.
            await h.wait_text("qwen-3.8-operator")
            await h.goto_row("item", "qwen-3.8-operator")
            await h.press("space")
            await h.goto_row("item", "gemma-4-26b")
            await h.press("space")
            await h.press("c")
            await h.wait_text("Type yes to download")
            await h.press(*"no", "enter")
            await h.wait_text("download cancelled")
            self.assertEqual(self.launches(), [])
            await h.press("c")
            await h.wait_text("Type yes to download")
            await h.press(*"yes", "enter")
            await h.wait_for(lambda: self.launches(), "the downloader run")
            self.assertEqual(self.launches()[0]["argv"],
                             ["--select", "qwen-3.8-operator", "--select", "gemma-4-26b"])
        self.run_picker(s, start="download")

    def test_rate_limiter_screen_persists_and_is_child_of_remote(self):
        async def s(app, pilot, h):
            await h.press("n")                       # the limiter lives on the Remote lane now
            await h.wait_screen("RateLimiterScreen")
            self.assertIn("NVIDIA Rate Limiter", h.title())
            self.assertTrue(any("Back to" in a for a in h.labels("actions")))
            before = next(a for a in h.labels("actions") if "Mode:" in a)
            await h.press("o")
            await h.wait_for(lambda: next(a for a in h.labels("actions") if "Mode:" in a) != before,
                             "mode to change")
            mode = next(a for a in h.labels("actions") if "Mode:" in a).split("Mode:", 1)[1].strip()
            self.assertEqual(self.saved_state()["rate_limiter"]["mode"], mode)
            await h.press("b")
            await h.wait_screen("RemoteScreen")
        self.run_picker(s, start="remote")


class PickerTerminalEdge(PTYCase):
    """Only what a real terminal can show."""

    def test_q_exits_zero_from_home(self):
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.send("q")
        self.assertEqual(self.exit_code(), 0)

    def test_ctrl_c_restores_terminal_modes(self):
        self.spawn("local")
        self.wait_for("Local Session Picker")
        raw = termios.tcgetattr(self.fd)
        self.assertFalse(raw[3] & termios.ICANON, "picker should be in raw mode while running")
        self.send("ctrl-c")
        self.assertEqual(self.exit_code(), 130)
        # Cooked mode + echo back on the pty, and the escape sequences that undo the
        # alternate screen, mouse reporting and hidden cursor were all emitted.
        cooked = termios.tcgetattr(self.fd)
        self.assertTrue(cooked[3] & termios.ICANON, "canonical mode not restored")
        self.assertTrue(cooked[3] & termios.ECHO, "echo not restored")
        self.assertIn("\x1b[?1049l", self.buf, "alternate screen was not left")
        self.assertIn("\x1b[?25h", self.buf, "cursor was not restored")
        self.assertIn("\x1b[?1000l", self.buf, "mouse reporting was not disabled")

    def test_bash_entry_points_reach_the_picker(self):
        # remote-session.sh with no alias -> Remote direct root (a real script, stubbed inventory)
        self.spawn(entry=[str(ROOT / "bin/remote-session.sh")])
        self.wait_for("Remote API Session Picker")
        self.send("q")
        self.assertEqual(self.exit_code(), 0)


if __name__ == "__main__":
    unittest.main()
