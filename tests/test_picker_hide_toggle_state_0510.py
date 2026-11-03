"""tests/test_picker_hide_toggle_state_0510.py — 0.51.0 item A: the API-keys hide/show toggle
must reveal its CURRENT state in the row label and confirm visibly that the new state was
saved (user 2026-11-03: "the toggle doesn't reveal its state and doesn't confirm visibly that
the new state is saved")."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import PilotCase  # noqa: E402


class HideToggleState(PilotCase):
    async def _open_keys(self, h):
        await h.press("k")
        await h.wait_screen("APIKeysScreen")

    def test_toggle_label_reveals_state_and_save_is_confirmed(self):
        async def s(app, pilot, h):
            notes = []
            app.notify = lambda *a, **k: notes.append(str(a[0]) if a else "")
            await self._open_keys(h)
            rows = h.labels("rows")
            self.assertIn("Hide nvidia in the choose-model list (currently shown)", rows[3])
            await h.goto_row("item", "hide:nvidia")
            await h.press("enter")
            await h.pilot.pause(0.4)
            rows = h.labels("rows")
            # the row now states the CURRENT state, not just the action to perform
            self.assertIn("Show nvidia in the choose-model list (currently hidden)", rows[3])
            # persisted through the save callback into session-menu.local.json
            self.assertIn("nvidia", (self.saved_state().get("hidden_providers") or []),
                          f"hidden_providers not saved: {self.saved_state()}")
            # and the save got a VISIBLE confirmation, not a silent rebuild
            self.assertTrue(any("Saved" in n and "nvidia" in n and "hidden" in n for n in notes),
                            f"no save-confirmation notification, got {notes}")
        self.run_picker(s)

    def test_toggle_back_restores_shown_state(self):
        async def s(app, pilot, h):
            await self._open_keys(h)
            await h.goto_row("item", "hide:nvidia")
            await h.press("enter")
            await h.pilot.pause(0.3)
            await h.goto_row("item", "show:nvidia")
            await h.press("enter")
            await h.pilot.pause(0.3)
            rows = h.labels("rows")
            self.assertIn("Hide nvidia in the choose-model list (currently shown)", rows[3])
            self.assertNotIn("nvidia", (self.saved_state().get("hidden_providers") or []))
        self.run_picker(s)


class BackendChooserOrder(PilotCase):
    """0.51.0 item D: on the backend manager the "Choose backend" chooser renders AHEAD of all
    menu options, and the selection marker says "selected", never "current" (the menu is about
    which backend to modify, not which is in use)."""

    def test_chooser_first_and_marker_says_selected(self):
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            ids = [w.id for w in app.query_one("#layout").children]
            self.assertIn("rows", ids)
            self.assertIn("actions", ids)
            self.assertLess(ids.index("rows"), ids.index("actions"),
                            f"chooser rows must render ahead of the actions pane, got {ids}")
            await h.goto_row("group", "backends")
            await h.press("right")
            text = h.text()
            self.assertIn("← selected", text)
            self.assertNotIn("← current", text)
        self.run_picker(s)


class RightClickCopy(PilotCase):
    """0.51.0 item E: right-click (mouse button 3) on a row copies that row's text; Cmd+C
    (super+c) stays bound to copy the terminal selection. A plain left click must NOT copy."""

    def test_right_click_copies_hovered_row_but_left_click_does_not(self):
        async def s(app, pilot, h):
            from textual.events import MouseUp
            from textual.widgets import OptionList
            copied = []
            app.copy_to_clipboard = lambda t: copied.append(t)
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            rows = app.query_one("#rows", OptionList)
            rows._mouse_hovering_over = 3   # the "Hide nvidia ... (currently shown)" row
            right = MouseUp(rows, 5, 3, 0, 0, 3, False, False, False, screen_x=5, screen_y=3)
            rows._on_mouse_up(right)
            await h.pilot.pause(0.2)
            self.assertTrue(any("choose-model list" in c for c in copied),
                            f"right-click did not copy the hovered row: {copied}")
            self.assertTrue(right._stop_propagation, "right-click must be consumed, not activate the row")
            # planted negative: a plain left click (button 1) without a drag copies nothing
            left = MouseUp(rows, 5, 3, 0, 0, 1, False, False, False, screen_x=5, screen_y=3)
            rows._drag_start = None
            rows._on_mouse_up(left)
            await h.pilot.pause(0.2)
            self.assertEqual(len(copied), 1, f"left click must not copy: {copied}")
        self.run_picker(s)

    def test_cmd_c_stays_bound_to_copy_selection(self):
        async def s(app, pilot, h):
            keys = [b.key for b in app.BINDINGS]
            self.assertIn("super+c", keys)
            self.assertIn("ctrl+c", keys)
        self.run_picker(s)


class RemoteHomeRow(PilotCase):
    """0.51.0 item B: on the remote picker (opened from home), Back to Home is a row exactly
    two lines after the last provider row (one blank line between), NOT an actions-pane entry;
    one less empty line before the first provider; the b key still works."""

    async def _open_remote_list(self, h):
        await h.press("r")
        await h.wait_screen("RemoteScreen")
        await h.press("c")                     # open the choose-model list
        await h.wait_for(lambda: bool(h.labels("rows")), "rows loaded")
        await h.pilot.pause(0.3)

    def test_back_row_two_lines_after_last_provider(self):
        async def s(app, pilot, h):
            await self._open_remote_list(h)
            rows = h.labels("rows")
            home_idx = next(i for i, r in enumerate(rows) if "Back to Home" in r)
            last_prov = max(i for i, r in enumerate(rows) if i < home_idx and r.strip())
            self.assertEqual(home_idx, last_prov + 2,
                             f"Back must sit exactly two lines after the last provider row: {rows}")
            self.assertEqual(rows[home_idx - 1], "", f"one blank line between: {rows}")
            acts = h.labels("actions")
            self.assertFalse(any("Back to Home" in a for a in acts),
                             f"Back must not also be in the actions pane: {acts}")
            rows_w = app.query_one("#rows")
            self.assertEqual(int(rows_w.styles.margin_top or 0), 0,
                             "one less empty line before the first provider (margin_top)")
        self.run_picker(s)

    def test_back_row_activates_home(self):
        async def s(app, pilot, h):
            await self._open_remote_list(h)
            await h.goto_row("group", "__home__")
            await h.press("enter")
            await h.wait_screen("HomeScreen")
        self.run_picker(s)

    def test_b_key_still_returns_home(self):
        async def s(app, pilot, h):
            await h.press("r")
            await h.wait_screen("RemoteScreen")
            await h.press("b")
            await h.wait_screen("HomeScreen")
        self.run_picker(s)


class CloudProbeVerdictSurfaced(unittest.TestCase):
    """0.51.0 item F: the Cloud Session Configuration "e" probe row must not look like a no-op —
    its verdict (refused on a free lane / not-eligible / eligible / unknown) is flashed and, for
    refused, shown in the info box."""

    def _screen(self):
        from picker_harness import ROOT
        sys.path.insert(0, str(ROOT / "bin"))
        import session_picker_model as m
        flashes = []
        s = m.Settings()
        s.on_flash = lambda msg: flashes.append(str(msg))
        return m, m.CloudConfigScreen(s), flashes

    def _probe(self, scr, payload):
        from unittest import mock
        fake = mock.Mock(stdout=payload, stderr="")
        with mock.patch("subprocess.run", return_value=fake):
            scr._run_probe()

    def test_refusal_is_flashed_and_shown_in_infobox(self):
        m, scr, flashes = self._screen()
        self._probe(scr, '{"verdict": "refused", "evidence": "this shell is a free lane (http://localhost:4141) — nothing to measure"}')
        self.assertEqual(scr.settings.eligibility["verdict"], "refused")
        self.assertTrue(any("refused" in f and "free lane" in f for f in flashes),
                        f"refusal must be flashed visibly, got {flashes}")
        self.assertIn("refused", scr.info_text(None).lower())

    def test_not_eligible_flashes_verdict(self):
        m, scr, flashes = self._screen()
        self._probe(scr, '{"verdict": "not-eligible", "evidence": "safety notice in stream"}')
        self.assertTrue(any("not-eligible" in f for f in flashes), flashes)
        self.assertIn("Cost note", scr.info_text(None))


class LowkeyApiGroup(PilotCase):
    """0.51.0 item G: lowkey's API chooser must actually load the remote roster (a row with an
    empty model_id is keyed on its alias, not dropped) regardless of build/load ordering."""

    def test_api_group_filled_from_remote_inventory(self):
        async def s(app, pilot, h):
            await h.pilot.pause(2.5)
            sm = app.screen_model
            self.assertEqual(len(sm.api_agents), 3,
                             f"api_agents must come from the remote roster: {sm.api_agents}")
            api = next(g for g in sm.accordion.groups if g.id == "api")
            ids = [i.id for i in api.items]
            self.assertEqual(set(ids), {"nvidia:nvidia-a", "gemini:gemini-flash", "cerebras:cerebras-oss"},
                             f"api rows keyed provider:model-or-alias: {ids}")
            self.assertTrue(all("@" not in i and not i.endswith(":") for i in ids), ids)
        self.run_picker(s, start="lowkey")


class RemoteLocalCapableFilterSort(unittest.TestCase):
    """0.51.0 (user, corrected to the REMOTE session picker): local-capable models (Laguna XS
    33B, Arctic Embed 0.3B, Kosmos 2 1.7B — small enough to fit in RAM) must be FILTERED OUT
    of the remote model list by default, and when shown they sit at the very BOTTOM of their
    provider group."""

    def _model(self):
        from picker_harness import ROOT
        sys.path.insert(0, str(ROOT / "bin"))
        import session_picker_model as m
        return m

    def _screen(self, agents, shown):
        m = self._model()
        s = m.Settings()
        s.local_capable_shown = shown
        capable = {a.model_id for a in agents if a.local_capable}
        s.catalog_local_capable = lambda p, mid: mid in capable
        return m.RemoteScreen(s, agents=agents)

    def _agents(self):
        m = self._model()
        return [
            m.RemoteAgent("laguna", "nvidia", "Laguna XS", "unknown", local_capable=True, model_id="laguna"),
            m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown", model_id="nvidia-a"),
            m.RemoteAgent("gemini-flash", "gemini", "Gemini Flash", "renewing_free", model_id="gemini-flash"),
            m.RemoteAgent("arctic", "nvidia", "Arctic Embed", "unknown", local_capable=True, model_id="arctic"),
        ]

    def test_local_capable_hidden_by_default(self):
        scr = self._screen(self._agents(), shown=False)
        for g in scr.accordion.groups:
            ids = [i.id for i in g.items]
            self.assertNotIn("laguna", ids)
            self.assertNotIn("arctic", ids)

    def test_when_shown_local_capable_sit_at_bottom_of_group(self):
        scr = self._screen(self._agents(), shown=True)
        nvidia = next(g for g in scr.accordion.groups if g.id == "nvidia")
        ids = [i.id for i in nvidia.items]
        self.assertEqual(ids, ["nvidia-a", "laguna", "arctic"],
                         f"local-capable rows must move to the very bottom of their group: {ids}")


class ClipboardMechanism(PilotCase):
    """0.51.0 regression guard (user 2026-11-03: the 0.50.8 changelog claimed 'copy works under
    csl on both routes' but it did not): copy_to_clipboard must reach the SYSTEM clipboard
    (pbcopy) even when PATH is stripped, as in a csl-launched child — OSC 52 alone is ignored
    by Terminal.app."""

    def test_copy_reaches_pbcopy_even_with_empty_path(self):
        async def s(app, pilot, h):
            import subprocess
            from unittest import mock
            calls = []

            def fake_run(cmd, *a, **k):
                calls.append((list(cmd), k.get("input")))
                return mock.Mock(returncode=0)

            with mock.patch.dict(os.environ, {"PATH": ""}), \
                    mock.patch.object(subprocess, "run", side_effect=fake_run):
                app.copy_to_clipboard("hello-clip")
            pb = [(c, i) for c, i in calls if c[0].endswith("pbcopy")]
            self.assertTrue(any(i == "hello-clip" for _, i in pb),
                            f"pbcopy must receive the text even with PATH='' (csl stripped env): {calls}")
        self.run_picker(s)


if __name__ == "__main__":
    unittest.main()
