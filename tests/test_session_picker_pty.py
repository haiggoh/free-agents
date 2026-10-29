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
from picker_harness import RECORDER, ROOT, PilotCase, PTYCase, inventory_stub  # noqa: E402


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
            await h.wait_text("DeepSeek R1 Architect")
            await h.press("1", "1", "enter")
            await h.settle(0.8)
            self.assertEqual(self.launches(), [])
        self.run_picker(s, start="local")


class PickerLaunch(PilotCase):
    async def _launch_first_local(self, h):
        await h.press("c")                           # model list is on demand ("Choose model…")
        await h.wait_text("DeepSeek R1 Architect")
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
            await h.expand_settings()              # 0.47.2: settings rows live under the folded group
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
            # The live filter box owns typing while the list is open (2026-10-21), so the
            # list closes first — 'y' reaches the Filter submenu from the actions menu.
            await h.press("escape")
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
        return h.wait_panel(f"{alias} is on disk but not registered")

    async def _click_red_row(self, h, alias="gone-model"):
        """The ONLY way the cleanup panel opens (user 2026-10-16: no automatic offer)."""
        await h.press("c")
        await h.settle(0.5)
        await h.open_group("Other")                     # family_for() has no rule for these
        await h.goto_row("item", alias)
        await h.press("enter")
        await h.wait_panel(f"{alias} cannot launch")

    def test_register_decline_launches_nothing(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.settle(0.5)
            await h.open_group("Other")                 # family_for() has no rule for it
            await h.goto_row("item", "auto-dream-7b")
            await h.press("enter")
            await self._prompt_offers_register(h)
            await h.press("escape")
            await h.wait_text("not registered")
            self.assertEqual(self.launches(), [])       # recorder logs every stub call: NONE ran
        self.run_picker(s, start="local")

    def test_register_accept_runs_register_then_launch(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.settle(0.5)
            await h.open_group("Other")                 # family_for() has no rule for it
            await h.goto_row("item", "auto-dream-7b")
            await h.press("enter")
            await self._prompt_offers_register(h)
            await h.press("enter")                      # default: Register and launch
            await h.wait_for(lambda: len(self.launches()) >= 2, "register + launch recorded")
            recs = self.launches()
            self.assertEqual([rec["prog"] for rec in recs], ["csl", "csl"])
            self.assertEqual(recs[0]["argv"], ["--register-model", "auto-dream-7b"])
            self.assertEqual(recs[1]["argv"][0], "--picker-launch")
            self.assertEqual(recs[1]["argv"][1], "auto-dream-7b")
        self.run_picker(s, start="local")

    def test_unregistered_model_is_yellow_and_pinned_entry_registers_without_launch(self):
        # User 2026-10-17: new on-disk models are easy to miss inside the folds; mark them
        # yellow and pin one "Register new local models found on disk" row above every group.
        async def s(app, pilot, h):
            from textual.widgets import OptionList
            await h.press("c")
            await h.settle(0.5)
            rows = app.query_one("#rows", OptionList)
            first = rows.get_option_at_index(0).prompt
            self.assertIn("Register new local models found on disk (1)", str(first))
            self.assertIn("yellow", str(getattr(first, "style", "")))
            await h.open_group("Other")
            await h.goto_row("item", "auto-dream-7b")
            row = rows.get_option_at_index(rows.highlighted).prompt
            self.assertIn("not registered yet", str(row))
            self.assertIn("yellow", str(getattr(row, "style", "")))
            await h.goto_row("group", self.m.NEW_MODELS_GROUP)
            await h.press("enter")
            await h.wait_panel("[x] auto-dream-7b")
            await h.press("enter")                      # default: Register selected
            await h.wait_for(lambda: self.launches(), "the register call")
            self.assertEqual([r["argv"] for r in self.launches()], [["--register-model", "auto-dream-7b"]],
                             "registers only — nothing is launched from this entry")
            await h.wait_text("registered 1 model")
            # After the reload the model is a plain registered row and the pinned entry is gone
            # (the stub now reports it registered, as the real csl does after --register-model).
            await h.wait_for(lambda: not any("Register new" in r for r in h.labels("rows")),
                             "the pinned register entry to disappear")
            await h.open_group("Other")
            await h.goto_row("item", "auto-dream-7b")
            row = rows.get_option_at_index(rows.highlighted).prompt
            self.assertNotIn("not registered", str(row))
            self.assertNotIn("yellow", str(getattr(row, "style", "")))
        # A stub that REMEMBERS the registration, like the real csl (user 2026-10-17: the old
        # fixture kept reporting it unregistered, so the row stayed yellow).
        reg = Path(self.tmp.name) / "registered"
        after = HYGIENE_INV.replace(AUTO_ROW, "auto-dream-7b\trapid\tmedium\t\t")
        Path(self.env["CSL_SELF"]).write_text(
            "#!/bin/sh\n"
            f'case "$1" in --register-model) touch "{reg}";; esac\n'
            f'for a in "$@"; do [ "$a" = --inventory ] && {{ if [ -e "{reg}" ]; then cat <<\'EOF\'\n{after}EOF\n'
            f"else cat <<'EOF'\n{HYGIENE_INV}EOF\nfi; exit 0; }}; done\n"
            + RECORDER.split("\n", 1)[1])
        self.run_picker(s, start="local")

    def test_cleanup_offer_is_a_panel_question_first(self):
        # User 2026-10-16: the typed yes/no sat ABOVE a grey explanation and read as detail.
        # Now a modal panel: the question is its first line, each missing model is listed.
        async def s(app, pilot, h):
            await self._click_red_row(h)
            first = h.panel_text().splitlines()[0]
            self.assertIn("missing from disk", first)
            self.assertIn("Remove selected from the list", h.panel_text())
            self.assertIn("Keep", h.panel_text())
        self.run_picker(s, start="local")

    def test_cleanup_accept_moves_missing_model_to_tombstone(self):
        async def s(app, pilot, h):
            await self._click_red_row(h)
            await h.press("enter")                      # the highlighted default: remove
            await h.wait_for(lambda: self.launches(), "the unregister call")
            rec = self.launches()[0]
            self.assertEqual(rec["prog"], "csl")
            self.assertEqual(rec["argv"], ["--unregister-model", "gone-model"])
            await h.wait_for(lambda: h.panel() is None, "panel closed")
            await h.settle(0.5)
            self.assertNotIn("gone-model", "\n".join(h.labels("rows")),
                             "a removed model must leave the list at once")
        self.run_picker(s, start="local")

    def test_cleanup_per_model_choice_removes_only_ticked(self):
        async def s(app, pilot, h):
            await self._click_red_row(h)
            # Only the clicked model is ticked; both boxes must actually DRAW (rendered text).
            self.assertIn("[x] gone-model", h.panel_text())
            self.assertIn("[ ] lost-model", h.panel_text())
            pick = h.panel().query_one("#cp-pick")
            pick.focus()
            pick.highlighted = pick.get_option_index("lost-model")
            await h.press("space")                      # tick it too: monochrome box, no colour
            await h.wait_panel("[x] lost-model")
            pick.highlighted = pick.get_option_index("gone-model")
            await h.press("space")                      # …and untick the clicked one
            await h.wait_panel("[ ] gone-model")
            h.panel().query_one("#cp-choices").focus()
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "the unregister call")
            self.assertEqual(self.launches()[0]["argv"], ["--unregister-model", "lost-model"])
        Path(self.env["CSL_SELF"]).write_text(inventory_stub(
            HYGIENE_INV + "#missing\tlost-model\tlost-model-v1\n"))
        self.run_picker(s, start="local")

    def test_cleanup_result_does_not_follow_you_out_of_the_local_lane(self):
        # User 2026-10-16: "moved N missing model(s)" stayed on Home and Remote.
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await self._click_red_row(h)
            await h.press("enter")
            await h.wait_text("moved 1 missing model")
            # 0.40.0: the open local list's live filter owns letters — Escape closes the list
            # first (as on the Remote screen), then B goes back.
            await h.press("escape")
            await h.press("b")
            await h.wait_screen("HomeScreen")
            self.assertNotIn("missing model", h.status())
            await h.press("r")
            await h.wait_screen("RemoteScreen")
            self.assertNotIn("missing model", h.status())
        self.run_picker(s)

    def test_entering_local_opens_no_panel_by_itself(self):
        # User 2026-10-16: the red rows are warning enough; the question waits for a click.
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.settle(1.0)
            self.assertIsNone(h.panel(), "no automatic cleanup offer on entering Local")
            await h.press("c")
            await h.settle(0.5)
            self.assertIsNone(h.panel())
            self.assertEqual(self.launches(), [])
        self.run_picker(s)

    def test_missing_model_row_is_red_and_says_so(self):
        # User 2026-10-16: a registered model whose weights are gone must not look launchable.
        async def s(app, pilot, h):
            from textual.widgets import OptionList
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
            await self._click_red_row(h)
            await h.press("escape")                     # Esc = keep
            await h.wait_text("kept 1 missing model")
            await h.press("enter")                      # the red row again: the panel comes back
            await h.wait_panel("gone-model cannot launch")
            await h.press("escape")
            self.assertEqual(self.launches(), [], "a missing model must never reach the launcher")
        self.run_picker(s, start="local")


class RegistryAutoSync(PilotCase):
    """Memory guard screen: "Auto-sync local registry with the disk" ON registers new on-disk
    models and moves missing ones to the removed list without asking; OFF is 0.36.2 (ask)."""

    def _stub(self):
        # A csl that remembers what was done, like the real one: after the sync the inventory
        # reports the new model registered and the missing one gone.
        done = Path(self.tmp.name) / "synced"
        after = "qwen-3.8-operator\trapid\thigh\t\t\nauto-dream-7b\trapid\tmedium\t\t\n"
        Path(self.env["CSL_SELF"]).write_text(
            "#!/bin/sh\n"
            f'case "$1" in --register-model|--unregister-model) touch "{done}";; esac\n'
            f'for a in "$@"; do [ "$a" = --inventory ] && {{ if [ -e "{done}" ]; then printf \'{after}\'; '
            f"else cat <<'EOF'\n{HYGIENE_INV}EOF\nfi; exit 0; }}; done\n"
            + RECORDER.split("\n", 1)[1])

    def _prefs(self, values):
        sys.path.insert(0, str(ROOT / "bin"))
        import session_menu_state as sms
        sms.save_memguard(self.cfg, values)

    def test_on_syncs_without_asking_and_says_what_it_did(self):
        self._stub()
        self._prefs({"registry_auto_sync": True})

        async def s(app, pilot, h):
            await h.wait_for(lambda: len(self.launches()) >= 2, "register + unregister calls")
            argvs = [r["argv"] for r in self.launches()]
            self.assertIn(["--register-model", "auto-dream-7b"], argvs)
            self.assertIn(["--unregister-model", "gone-model"], argvs)
            self.assertFalse(any(a and a[0] == "--picker-launch" for a in argvs), "nothing launched")
            self.assertIsNone(h.panel(), "no question asked")
            await h.wait_text("auto-synced the registry: registered 1, moved 1 missing")
            await h.press("c")
            await h.settle(0.5)
            self.assertFalse(any("Register new" in r for r in h.labels("rows")))
            self.assertFalse(any("weights missing" in r for r in h.labels("rows")))
        self.run_picker(s, start="local")

    def test_off_is_unchanged_nothing_runs(self):
        self._stub()

        async def s(app, pilot, h):
            await h.press("c")
            await h.settle(0.8)
            self.assertEqual(self.launches(), [])
            self.assertTrue(any("Register new" in r for r in h.labels("rows")))
        self.run_picker(s, start="local")


class PickerTools(PilotCase):
    def test_downloader_lists_catalog_cancel_zero_calls_confirm_exact_aliases(self):
        async def s(app, pilot, h):
            # Regression (0.22.3-0.23.0): the catalog was never loaded, so this list was empty.
            await h.wait_text("Alibaba Qwen 3.8 Operator")
            await h.goto_row("item", "qwen-3.8-operator")
            await h.press("space")
            await h.goto_row("item", "gemma-4-26b")
            await h.press("space")
            await h.press("c")
            await h.wait_panel("Download 2 model(s)")
            await h.press("escape")
            await h.wait_text("download cancelled")
            self.assertEqual(self.launches(), [])
            await h.press("c")
            await h.wait_panel("Download 2 model(s)")
            await h.press("enter")                      # default: Download now
            await h.wait_for(lambda: self.launches(), "the downloader run")
            self.assertEqual(self.launches()[0]["argv"],
                             ["--select", "qwen-3.8-operator", "--select", "gemma-4-26b"])
        self.run_picker(s, start="download")

    def test_rate_limiter_reset_is_a_panel_and_defaults_to_cancel(self):
        async def s(app, pilot, h):
            state = Path(self.tmp.name) / "limiter-state.json"
            state.write_text("{}")
            os.environ["LA_NVIDIA_THROTTLE_STATE"] = str(state)
            self.addCleanup(os.environ.pop, "LA_NVIDIA_THROTTLE_STATE", None)
            await h.press("n")
            await h.wait_screen("RateLimiterScreen")
            await h.press("x")
            await h.wait_panel("Delete the shared rate-limiter state file?")
            await h.press("enter")                   # default = Cancel: deletes nothing
            await h.wait_text("limiter state unchanged")
            self.assertTrue(state.exists())
            await h.press("x")
            await h.wait_panel("Delete the shared rate-limiter state file?")
            await h.press("down", "enter")           # Delete the state file
            await h.wait_for(lambda: not state.exists(), "the state file to go")
        self.run_picker(s, start="remote")

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
            await h.press("escape")                            # the live filter owns typing: Esc closes
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


class LocalLiveFilter(PilotCase):
    """The Remote list's live filter on the LOCAL list too (user 2026-10-22): the box opens
    with the list, holds focus, narrows from the first character, Escape closes the list."""

    def test_typing_filters_the_local_list(self):
        async def s(app, pilot, h):
            from textual.widgets import Input
            await h.press("c")
            await h.wait_text("DeepSeek R1 Architect")
            box = app.query_one("#search", Input)
            self.assertTrue(box.display, "the filter box is part of the open local list")
            self.assertIs(app.focused, box)
            await h.press(*"qwen")
            rows = h.labels("rows")
            self.assertTrue(any("Alibaba Qwen 3.8 Operator" in r for r in rows), rows)
            self.assertFalse(any("DeepSeek R1 Architect" in r for r in rows), rows)
            self.assertFalse(any("Meta Llama 4 Scout" in r for r in rows), rows)
            for _ in range(4):
                await h.press("backspace")
            await h.wait_text("DeepSeek R1 Architect")
        self.run_picker(s, start="local")

    def test_enter_launches_the_filtered_model(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("DeepSeek R1 Architect")
            await h.press(*"architect")
            await h.press("down")
            await h.press("down")
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "a recorded launch")
            self.assertEqual(self.launches()[0]["argv"][1], "deepseek-r1-architect")
        self.run_picker(s, start="local")


class LocalInfoBox(PilotCase):
    def test_info_box_shows_the_alias_of_the_highlighted_model(self):
        """0.41.0: local rows read as names, so the exact alias -- what `csl <alias>`
        takes -- lives in the info box under the open list, as on the Remote screen."""
        async def s(app, pilot, h):
            from textual.widgets import Static
            await h.press("c")
            await h.wait_text("DeepSeek R1 Architect")
            await h.goto_row("item", "deepseek-r1-architect")
            await h.settle(0.3)
            info = app.query_one("#info", Static)
            self.assertTrue(info.display, "the local list shows the info box")
            self.assertIn("Alias: deepseek-r1-architect", str(info.render()))
        self.run_picker(s, start="local")


class InputFrames(PilotCase):
    def test_every_input_keeps_the_round_frame_when_focused(self):
        """User 2026-10-22: the symmetric rounded frame must be on EVERY text input. Textual's
        own Input:focus rule draws `tall`; ours must win in the focused state too."""
        async def s(app, pilot, h):
            from textual.widgets import Input
            await h.press("c")
            await h.wait_text("DeepSeek R1 Architect")
            inputs = list(app.query(Input))
            self.assertGreaterEqual(len(inputs), 2, [i.id for i in inputs])   # search + prompt
            box = app.query_one("#search", Input)
            self.assertIs(app.focused, box)
            for w in inputs:
                for edge in (w.styles.border_left, w.styles.border_right, w.styles.border_top):
                    self.assertEqual(edge[0], "round", f"#{w.id}: {edge}")
        self.run_picker(s, start="local")


class RemoteLiveFilter(PilotCase):
    """The live model filter (user, 2026-10-21): the input line sits at the top of the open
    remote model list, takes focus the moment the list opens, and narrows the list from the
    FIRST typed character — no separate search action row, no Enter to apply."""

    def test_box_opens_with_the_list_and_filters_from_the_first_character(self):
        async def s(app, pilot, h):
            from textual.widgets import Input
            await h.press("c")
            await h.wait_text("NVIDIA A")
            box = app.query_one("#search", Input)
            self.assertTrue(box.display, "the filter box is part of the open list")
            self.assertIs(app.focused, box, "typing works the moment the list opens")
            # One character already narrows: 'v' matches NVIDIA A, not Gemini Flash. The
            # GROUP HEADER is the discriminator — the folded group already hides its items
            # in the unfiltered list, so asserting on an item would pass with no filter.
            # CONTENTION NOTE (2026-10-25, the fa-gate-test-flakes pattern): the press()
            # helper's fixed 0.15 s gap is not a promise the app has PROCESSED the key —
            # under a loaded gate the labels were still the unfiltered list and the test
            # failed on a pass-anywhere assertion. Condition waits on the discriminating
            # state, never a fixed sleep.
            await h.press("v")
            await h.wait_for(lambda: not any("Gemini (1)" in r for r in h.labels("rows")),
                             "the 'v' filter to fold Gemini away", secs=8)
            self.assertTrue(any("NVIDIA A" in r for r in h.labels("rows")), h.labels("rows"))
            await h.press("backspace")               # an empty box shows every provider again
            await h.wait_for(lambda: any("Gemini (1)" in r for r in h.labels("rows")),
                             "the empty filter to show every provider again", secs=8)
        self.run_picker(s, start="remote")

    def test_digits_filter_too_and_nomatch_points_at_the_box(self):
        # The needle must be digits-LED (the point: numbers filter too) but collision-proof:
        # a bare "7" matches real catalog ids (Cerebras lists llama-3.3-70b), and under load
        # the search triggers every provider's catalog to load unprompted
        # (session_picker_model:1277) — so a bare digit is machine-state-dependent and
        # raced the loader in 3 of 4 gate runs (measured 2026-10-24).
        async def s(app, pilot, h):
            from textual.widgets import Input
            await h.press("c")
            await h.wait_text("NVIDIA A")
            await h.press("7", "z", "9", "q")        # digits filter too, not just letters
            self.assertEqual(app.query_one("#search", Input).value, "7z9q")
            rows = h.labels("rows")
            self.assertTrue(any("No model matches" in r for r in rows), rows)
            self.assertTrue(any("Edit the live filter" in r for r in rows), rows)
        self.run_picker(s, start="remote")

    def test_enter_from_the_box_launches_the_highlighted_model(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("NVIDIA A")
            await h.press("v")
            await h.goto_row("item", "nvidia-a")     # downs move the list; the caret stays
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "a recorded launch")
            rec = self.launches()[0]
            self.assertEqual(rec["prog"], "remote-session.sh")
            self.assertEqual(rec["argv"][-1], "nvidia-a")
        self.run_picker(s, start="remote")

    def test_typing_with_focus_on_the_list_lands_in_the_box(self):
        async def s(app, pilot, h):
            from textual.widgets import Input, OptionList
            await h.press("c")
            await h.wait_text("NVIDIA A")
            app.query_one("#rows", OptionList).focus()   # e.g. after a mouse click
            await h.press("v")
            box = app.query_one("#search", Input)
            self.assertEqual(box.value, "v")
            self.assertIs(app.focused, box, "focus follows the typing into the box")
            self.assertFalse(any("Gemini Flash" in r for r in h.labels("rows")))
        self.run_picker(s, start="remote")

    def test_escape_closes_the_list_and_the_filter_survives_reopening(self):
        async def s(app, pilot, h):
            from textual.widgets import Input
            await h.press("c")
            await h.wait_text("NVIDIA A")
            await h.press("v")
            await h.press("escape")                     # first escape closes the list, not the app
            box = app.query_one("#search", Input)
            self.assertFalse(box.display)
            self.assertEqual(h.screen(), "RemoteScreen", "still on the remote screen")
            await h.press("c")                       # reopen: the box carries the filter
            self.assertEqual(box.value, "v")
            self.assertFalse(any("Gemini Flash" in r for r in h.labels("rows")))
        self.run_picker(s, start="remote")


    def test_every_text_input_wears_a_symmetric_border(self):
        """No input may render Textual's default `tall` border — its left side draws barbs
        the right side has not (user 2026-10-22: "the left side has barbs protruding
        outward"). Every text input wears the same rounded frame as the info box."""
        async def s(app, pilot, h):
            from textual.widgets import Input
            await h.press("c")
            await h.wait_text("NVIDIA A")
            for w, name in ((app.query_one("#search", Input), "#search"),
                            (app.query_one("#prompt", Input), "#prompt")):
                sides = (w.styles.border_top, w.styles.border_right,
                         w.styles.border_bottom, w.styles.border_left)
                for side in sides:
                    self.assertEqual(side[0], "round",
                                     f"{name} border is not round on every side: {sides!r}")
        self.run_picker(s, start="remote")


if __name__ == "__main__":
    unittest.main()
