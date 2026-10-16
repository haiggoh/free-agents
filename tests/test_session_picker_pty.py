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
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import ROOT, PilotCase, PTYCase, inventory_stub  # noqa: E402


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

    def test_failed_launch_pauses_so_its_error_stays_readable(self):
        """User 2026-10-15 (the deepseek-v4 silent bounce): a launch that fails must NOT be painted
        over by the menu. The child's output stays on screen until Enter, like a tool's."""
        os.environ["STUB_RC"] = "7"
        self.addCleanup(os.environ.pop, "STUB_RC", None)
        import builtins
        prompts = []

        async def s(app, pilot, h):
            builtins.input = lambda *a, **k: prompts.append(a[0] if a else "") or ""
            await self._launch_first_local(h)
            await h.wait_text("last child exited 7")
        self.run_picker(s, start="local")
        self.assertTrue(any("exit 7" in p for p in prompts), f"no pause after the failed launch: {prompts}")

    def test_successful_launch_does_not_pause(self):
        import builtins
        prompts = []

        async def s(app, pilot, h):
            builtins.input = lambda *a, **k: prompts.append(a[0] if a else "") or ""
            await self._launch_first_local(h)
            await h.settle(0.5)
        self.run_picker(s, start="local")
        self.assertEqual(prompts, [], "a session that exits cleanly returns straight to the menu")

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


AUTO_ROW = "auto-dream-7b\trapid (auto)\tmedium\tauto-scanned\tauto-dream-7b"
HYGIENE_INV = "\n".join([
    "qwen-3.8-operator\trapid\thigh\t\t",
    AUTO_ROW,
    "#missing\tgone-model\tgone-model-v2",       # registered, weights deleted from disk
]) + "\n"


class PickerRegistryOffers(PilotCase):
    """Selecting an auto-scanned (on-disk, unregistered) row offers registration instead of
    bouncing; entering the local lane offers to move registered-but-missing models to the
    removed-models tombstone. Both flows drive csl subcommands through the recorder stub."""

    def setUp(self):
        super().setUp()
        Path(self.env["CSL_SELF"]).write_text(inventory_stub(HYGIENE_INV))

    def _prompt_offers_register(self, h, alias="auto-dream-7b"):
        return h.wait_text(f"Register {alias}")

    def test_register_decline_launches_nothing(self):
        async def s(app, pilot, h):
            await h.wait_panel("missing from disk")     # cleanup offer is open first; Esc keeps
            await h.press("escape")
            await h.wait_text("kept 1 missing model")
            await h.press("c")
            await h.settle(0.5)
            await h.open_group("Other")                 # family_for() has no rule for it
            await h.goto_row("item", "auto-dream-7b")
            await h.press("enter")
            await self._prompt_offers_register(h)
            await h.press(*"no", "enter")
            await h.wait_text("not registered")
            self.assertEqual(self.launches(), [])       # recorder logs every stub call: NONE ran
        self.run_picker(s, start="local")

    def test_register_accept_runs_register_then_launch(self):
        async def s(app, pilot, h):
            await h.wait_panel("missing from disk")
            await h.press("escape")
            await h.wait_text("kept 1 missing model")
            await h.press("c")
            await h.settle(0.5)
            await h.open_group("Other")                 # family_for() has no rule for it
            await h.goto_row("item", "auto-dream-7b")
            await h.press("enter")
            await self._prompt_offers_register(h)
            await h.press(*"yes", "enter")
            await h.wait_for(lambda: len(self.launches()) >= 2, "register + launch recorded")
            recs = self.launches()
            self.assertEqual([rec["prog"] for rec in recs], ["csl", "csl"])
            self.assertEqual(recs[0]["argv"], ["--register-model", "auto-dream-7b"])
            self.assertEqual(recs[1]["argv"][0], "--picker-launch")
            self.assertEqual(recs[1]["argv"][1], "auto-dream-7b")
        self.run_picker(s, start="local")

    def test_cleanup_offer_is_a_panel_question_first(self):
        # User 2026-10-16: the typed yes/no sat ABOVE a grey explanation and read as detail.
        # Now a modal panel: the question is its first line, each missing model is listed.
        async def s(app, pilot, h):
            await h.wait_panel("gone-model")
            first = h.panel_text().splitlines()[0]
            self.assertIn("missing from disk", first)
            self.assertIn("Remove selected from the list", h.panel_text())
            self.assertIn("Keep", h.panel_text())
        self.run_picker(s, start="local")

    def test_cleanup_accept_moves_missing_model_to_tombstone(self):
        async def s(app, pilot, h):
            await h.wait_panel("gone-model")
            await h.press("enter")                      # the highlighted default: remove
            await h.wait_for(lambda: self.launches(), "the unregister call")
            rec = self.launches()[0]
            self.assertEqual(rec["prog"], "csl")
            self.assertEqual(rec["argv"], ["--unregister-model", "gone-model"])
            await h.wait_for(lambda: h.panel() is None, "panel closed")
            await h.press("c")
            await h.settle(0.5)
            self.assertNotIn("gone-model", "\n".join(h.labels("rows")),
                             "a removed model must leave the list at once")
        self.run_picker(s, start="local")

    def test_cleanup_per_model_choice_removes_only_ticked(self):
        async def s(app, pilot, h):
            await h.wait_panel("[x] lost-model")         # all ticked by default
            self.assertIn("[x] gone-model", h.panel_text())
            pick = h.panel().query_one("#cp-pick")
            pick.focus()
            pick.highlighted = pick.get_option_index("lost-model")
            await h.press("space")                      # untick it: monochrome box, no colour
            await h.wait_panel("[ ] lost-model")
            h.panel().query_one("#cp-choices").focus()
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "the unregister call")
            self.assertEqual(self.launches()[0]["argv"], ["--unregister-model", "gone-model"])
        Path(self.env["CSL_SELF"]).write_text(inventory_stub(
            HYGIENE_INV + "#missing\tlost-model\tlost-model-v1\n"))
        self.run_picker(s, start="local")

    def test_cleanup_result_does_not_follow_you_out_of_the_local_lane(self):
        # User 2026-10-16: "moved N missing model(s)" stayed on Home and Remote.
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.wait_panel("gone-model")
            await h.press("enter")
            await h.wait_text("moved 1 missing model")
            await h.press("b")
            await h.wait_screen("HomeScreen")
            self.assertNotIn("missing model", h.status())
            await h.press("r")
            await h.wait_screen("RemoteScreen")
            self.assertNotIn("missing model", h.status())
        self.run_picker(s)

    def test_cleanup_offered_once_per_session(self):
        # Starts at Home: only a Home-owned Local lane has Back (a direct-root lane has Quit).
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.wait_panel("gone-model")
            await h.press("escape")                     # decline
            await h.wait_text("kept 1 missing model")
            await h.press("b")                          # home
            await h.wait_screen("HomeScreen")
            await h.press("l")                          # local again
            await h.wait_screen("LocalScreen")
            await h.settle(1.0)
            self.assertIsNone(h.panel(), "cleanup offer must not reopen by itself within a session")
            self.assertEqual(self.launches(), [])
        self.run_picker(s)

    def test_missing_model_row_is_red_and_says_so(self):
        # User 2026-10-16: a registered model whose weights are gone must not look launchable.
        async def s(app, pilot, h):
            from textual.widgets import OptionList
            await h.wait_panel("gone-model")
            await h.press("escape")
            await h.press("c")
            await h.settle(0.5)
            await h.open_group("Other")                 # family_for() has no rule for it
            await h.goto_row("item", "gone-model")
            rows = app.query_one("#rows", OptionList)
            prompt = rows.get_option_at_index(rows.highlighted).prompt
            self.assertIn("weights missing", str(prompt))
            self.assertTrue(any("red" in str(sp.style) for sp in getattr(prompt, "spans", []))
                            or "red" in str(getattr(prompt, "style", "")), f"not red: {prompt!r}")
            self.assertIn("2 model(s) on disk", app.screen_model.policy, "a missing model is not counted as on disk")
        self.run_picker(s, start="local")

    def test_selecting_missing_model_reopens_cleanup_never_launches(self):
        async def s(app, pilot, h):
            await h.wait_panel("gone-model")
            await h.press("escape")                     # the automatic offer is declined…
            await h.press("c")
            await h.settle(0.5)
            await h.open_group("Other")                 # family_for() has no rule for it
            await h.goto_row("item", "gone-model")
            await h.press("enter")                      # …but clicking the red row brings it back
            await h.wait_panel("gone-model cannot launch")
            await h.press("escape")
            self.assertEqual(self.launches(), [], "a missing model must never reach the launcher")
        self.run_picker(s, start="local")


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


class RemoteInfoBox(PilotCase):
    def test_info_box_follows_highlight_and_hides_with_list(self):
        async def s(app, pilot, h):
            info = app.query_one("#info")
            self.assertFalse(info.display)                    # model list closed: no box
            await h.press("c")
            await h.settle()
            self.assertTrue(info.display)
            rows = app.query_one("#rows")
            rows.focus()
            idx = next(i for i, r in enumerate(app.row_ids) if r and r[0] == "item")
            rows.highlighted = idx
            await pilot.pause(0.2)
            self.assertIn(app.screen_model.agents[0].display, str(info.render()))
            app.on_hover_option_list_hovered(app.query_one("#rows").Hovered(0))   # group header
            self.assertIn("Hover or highlight", str(info.render()))
            await h.press("c")                                 # hide the list again
            self.assertFalse(info.display)
            await h.press("q")
        self.run_picker(s, start="remote")


class ThemeScreenColors(PilotCase):
    """The theme rows must behave exactly like the effort/temperature rows (user 2026-10-15):
    left = step back, right = step forward, Enter = right, mouse click = Enter, wrapping
    through every preset. Labels spell out the color name (accessibility), and persistence
    goes ONLY to LA_THEME_CONFIG_DIR — 0.34.13 shipped a cycled-over default because live
    testing wrote into the repo's config/."""

    def seed(self):
        import shutil
        for name in ("local-theme.json", "remote-theme.json"):
            shutil.copy(ROOT / "config" / name, self.themes / name)

    def repo_accents(self):
        import json
        return [json.loads((ROOT / "config" / n).read_text())["theme"]["accent_color"]
                for n in ("local-theme.json", "remote-theme.json")]

    def lane_label(self, h, which):
        marker = "Local" if which == "local" else "Remote"
        return next(lbl for lbl in h.labels("actions") if marker in lbl)

    def test_local_row_arrows_enter_click_and_wrap(self):
        self.seed()
        async def s(app, pilot, h):
            await h.press("h")
            await h.wait_screen("ThemeConfigScreen")
            self.assertIn("Yellow", self.lane_label(h, "local"))        # name spelled out
            self.assertIn("#FFD700", self.lane_label(h, "local"))
            await h.press("right")
            self.assertIn("Magenta", self.lane_label(h, "local"))       # right steps forward
            await h.press("left")
            self.assertIn("Yellow", self.lane_label(h, "local"))        # left steps back
            await h.press("left")
            self.assertIn("Orange Red", self.lane_label(h, "local"))    # keeps stepping back
            await h.press("enter")                                      # enter == right arrow
            self.assertIn("Yellow", self.lane_label(h, "local"))
            idx = next(i for i, a in enumerate(app.action_ids) if a.key == "l")
            app._activate_action(idx)                                   # what a row click routes to
            self.assertIn("Magenta", self.lane_label(h, "local"))
        self.run_picker(s)
        import json
        saved = json.loads((self.themes / "local-theme.json").read_text())
        self.assertEqual(saved["theme"]["accent_color"], "#FF00FF")     # persisted to the override
        self.assertEqual(self.repo_accents(), ["#FFD700", "#ADFF2F"])   # repo untouched

    def test_remote_row_steps_and_d_restores(self):
        self.seed()
        async def s(app, pilot, h):
            await h.press("h")
            await h.wait_screen("ThemeConfigScreen")
            idx = next(i for i, a in enumerate(app.action_ids) if a.key == "r")
            app._activate_action(idx)                                   # step the remote row
            self.assertIn("#EE2B2B", self.lane_label(h, "remote"))      # Lime -> Fire Red
            await h.press("d")
            self.assertIn("#ADFF2F", self.lane_label(h, "remote"))      # restored
            self.assertIn("#FFD700", self.lane_label(h, "local"))       # local default
        self.run_picker(s)
        import json
        saved = json.loads((self.themes / "remote-theme.json").read_text())
        self.assertEqual(saved["theme"]["accent_color"], "#ADFF2F")
        self.assertEqual(self.repo_accents(), ["#FFD700", "#ADFF2F"])

    def test_lane_row_prompt_keeps_rich_text_styles(self):
        """User 2026-10-15: swatch, hex colour, border slice and fixed swatch column must
        RENDER. render_model used to f-string the label into the Option prompt, flattening
        the rich Text to a plain str — the styles existed on the label object and still
        never displayed, which str()-based assertions structurally could not detect."""
        self.seed()
        async def s(app, pilot, h):
            from rich.text import Text
            await h.press("h")
            await h.wait_screen("ThemeConfigScreen")
            from textual.widgets import OptionList
            ol = app.query_one("#actions", OptionList)
            idx = next(i for i, a in enumerate(app.action_ids) if a.key == "l")
            prompt = ol.get_option_at_index(idx).prompt
            self.assertIsInstance(prompt, Text,
                                  f"prompt flattened to {type(prompt).__name__}: {prompt!r}")
            styles = " ".join(str(span.style) for span in prompt.spans)
            self.assertIn("on #FFD700", styles, f"no accent swatch in spans: {styles!r}")
            self.assertIn("on #C6B039", styles, f"no border slice in spans: {styles!r}")
            self.assertIn("#FFD700", styles, f"hex not rendered in its own colour: {styles!r}")
            ridx = next(i for i, a in enumerate(app.action_ids) if a.key == "r")
            rprompt = ol.get_option_at_index(ridx).prompt
            self.assertIsInstance(rprompt, Text)
            self.assertEqual(len(str(prompt)), len(str(rprompt)),
                             f"lane rows misaligned: {prompt!s} | {rprompt!s}")
            width_before = len(str(prompt))
            app._activate_action(idx)                # step to Magenta (name 1 char longer)
            prompt2 = ol.get_option_at_index(idx).prompt
            self.assertEqual(len(str(prompt2)), width_before,
                             f"swatch column moved: {prompt!s} -> {prompt2!s}")
        self.run_picker(s)


if __name__ == "__main__":
    unittest.main()
