#!/usr/bin/env python3
"""tests/test_0564_user_issues.py — user-reported issues found testing v0.56.4 (2026-11-11):
1. API keys setup list must not change when a provider is hidden from the model list.
2. API keys setup: Back is a row AFTER the provider accordion, two blank rows of spacing.
3. Lowkey effort offers the same values as the other lanes (max / not set present).
4. Effort/Temperature/Thinking: one template — same order, hotkeys and values on all three
   lanes (local, remote, lowkey); remembered values stay per-lane.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_menu_state as sms  # noqa: E402
import session_picker_model as m  # noqa: E402


class EffortTemplateTests(unittest.TestCase):
    def test_one_effort_template_all_lanes(self):
        self.assertIn("max", sms.EFFORT_TEMPLATE)
        self.assertIn(sms.PROVIDER_DEFAULT, sms.EFFORT_TEMPLATE)
        self.assertNotIn("none", sms.EFFORT_TEMPLATE)      # the old rapid-only lowkey set is gone
        for lane in ("local_session", "remote_api_session", "lowkey"):
            self.assertEqual(sms.ALLOWED_EFFORT[lane], sms.EFFORT_TEMPLATE, lane)

    def test_model_settings_rows_same_order_keys_values_on_all_lanes(self):
        s = m.Settings()
        agents = [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown")]
        screens = {
            "local": m.LocalScreen(s, [], owner=m.HOME_OWNED),
            "remote": m.RemoteScreen(s, agents, owner=m.HOME_OWNED),
            "lowkey": m.LowkeyScreen(s, [], api_agents=agents),
        }
        per_lane = {}
        for lane, scr in screens.items():
            rows = [a for a in scr.actions() if a.key in ("e", "o", "j")]   # the 3 model-setting rows
            per_lane[lane] = [(a.key, tuple(a.choices), tuple(a.labels or ())) for a in rows]
        first = per_lane["local"]
        self.assertEqual([k for k, _, _ in first], ["e", "o", "j"], first)   # Effort, Temperature, Thinking
        for lane in ("remote", "lowkey"):
            self.assertEqual(per_lane[lane], first,
                             f"{lane} model rows differ from local: {per_lane[lane]} vs {first}")


class APIKeysScreenTests(unittest.TestCase):
    def test_keys_list_ignores_hidden_providers(self):
        s = m.Settings()
        s.hidden_providers = ["nvidia"]
        scr = m.APIKeysScreen(s, owner=m.DIRECT_ROOT)
        prov = {p[0] for p in m.APIKeysScreen.PROVIDERS}
        # even the hidden provider stays listed in the collapsed default view, without a
        # "hidden" annotation — the Hide/Show item inside its group states the toggle
        self.assertIn("nvidia", {g.id for g in scr.accordion.groups})
        scr._show_all_providers = True
        scr._rebuild_groups()
        listed = [g.id for g in scr.accordion.groups if g.id in prov]
        self.assertEqual(len(listed), len(prov),
                         "every provider stays listed no matter the hide toggle")
        self.assertFalse(any("hidden from model list" in g.label for g in scr.accordion.groups),
                         "the keys list must not change when a provider is hidden")

    def test_back_follows_the_list_via_compact_rows(self):
        # 0.56.5 (user): API keys uses the backend manager's compact-rows layout — Back
        # stays the last actions-pane item but the pane follows the accordion after two
        # blank rows (#gap height: 2), not pinned to the window bottom. The PTY-level
        # assertion on the layout class lives in test_session_picker_pty.py.
        scr = m.APIKeysScreen(m.Settings(), owner=m.HOME_OWNED)
        nav = [a for a in scr.actions() if a.section == "nav"]
        self.assertEqual([a.key for a in nav], ["b"])
        self.assertIn("Back to Home", nav[0].label)
        self.assertEqual(scr.handle_key("b"), m.Nav("home", m.HOME_OWNED))
        # no structural rows pollute the provider list
        self.assertFalse(any(g.id.startswith("__sp") or g.id == "__back__"
                             for g in scr.accordion.groups))


class ModernTilesTests(unittest.TestCase):
    def test_home_nav_rows_classify_as_nav_not_select(self):
        # 2026-11-19 user report: modern mode rendered an EMPTY tile grid. Root cause:
        # the rebase merged main's Action default choices=() (empty tuple), and
        # render_plan's `a.choices is not None` predicate then misclassified every nav
        # row as "select", so app.py's tiling (kind in nav/button) matched nothing.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin" / "fa_gui"))
        import picker_core as pc
        from fa_gui import adapter
        s = m.Settings()
        s.hw_hardware = "mlx"
        scr = pc.build_screen(m.Nav("home", m.HOME_OWNED), s, {"local": [], "remote": []}).screen
        kinds = {c.action_id: c.kind for c in adapter.render_plan(scr)}
        self.assertEqual(kinds.get("HomeScreen.l"), "nav", kinds)
        self.assertEqual(kinds.get("HomeScreen.q"), "nav", kinds)
        self.assertGreater(sum(1 for c in kinds.values() if c in ("nav", "button")), 0)


try:
    import webview  # noqa: F401  (GUI-venv only; the patch tests skip without it)
    _HAVE_PYWEBVIEW = True
except ImportError:
    _HAVE_PYWEBVIEW = False


class VibrancyMaterialTests(unittest.TestCase):
    def test_material_map_is_binary(self):
        # 2026-11-20 spec: light -> mediumLight, dark -> ultraDark (spike-verified in Task 5)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin" / "fa_gui"))
        from fa_gui.native_window import material_name_for
        self.assertEqual(material_name_for(True), "ultraDark")
        self.assertEqual(material_name_for(False), "mediumLight")

    def test_follow_system_key_defaults_off_and_validates(self):
        import fa_gui.appearance as ap
        self.assertIs(ap.DEFAULTS["follow_system_appearance"], False)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.json"
            with self.assertRaises(ValueError):
                ap.save(p, {"follow_system_appearance": "yes"})

    def test_dark_mode_resolution_store_wins_by_default(self):
        from fa_gui.native_window import resolve_dark_mode, is_system_dark
        # store wins when follow_system_appearance is off (the default)
        self.assertTrue(resolve_dark_mode({"dark_mode": True, "follow_system_appearance": False}, system_dark=False))
        self.assertFalse(resolve_dark_mode({"dark_mode": False, "follow_system_appearance": False}, system_dark=True))
        # system wins when the opt-in is on
        self.assertFalse(resolve_dark_mode({"dark_mode": True, "follow_system_appearance": True}, system_dark=False))
        self.assertTrue(resolve_dark_mode({"dark_mode": False, "follow_system_appearance": True}, system_dark=True))
        self.assertTrue(is_system_dark("NSAppearanceNameDarkAqua"))
        self.assertFalse(is_system_dark("NSAppearanceNameAqua"))
        self.assertFalse(is_system_dark(None))

    @unittest.skipUnless(_HAVE_PYWEBVIEW, "pywebview lives only in the GUI venv")
    def test_patch_applies_idempotently_and_marks_wrapper(self):
        import webview
        from fa_gui import native_window as nw
        from webview.platforms import cocoa
        nw.apply_vibrancy_patch()
        self.assertTrue(getattr(cocoa.BrowserView.__init__, "_fa_vibrancy_patch", False))
        wrapped = cocoa.BrowserView.__init__
        nw.apply_vibrancy_patch()   # idempotent: no double wrap
        self.assertIs(cocoa.BrowserView.__init__, wrapped)

    def test_patch_without_pywebview_is_a_noop(self):
        # runs everywhere: with webview present it is popped from sys.modules for the call
        import sys as _sys
        from fa_gui import native_window as nw
        saved = _sys.modules.pop("webview", None)
        try:
            nw.apply_vibrancy_patch()   # must not raise without pywebview
        finally:
            if saved is not None:
                _sys.modules["webview"] = saved


class RemoteFilterRowTests(unittest.TestCase):
    def test_remote_actions_single_filter_row_with_nav_target(self):
        # 2026-11-19 user report: the remote lane rendered EMPTY — RemoteScreen.actions()
        # raised "duplicate shortcut 'y': 'Filter settings…'" because a rebase kept BOTH
        # the in-list row (main's, with nav_target) and the moved row (the branch's).
        s = m.Settings()
        agents = [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown")]
        scr = m.RemoteScreen(s, agents, owner=m.HOME_OWNED)
        acts = scr.actions()          # check_action_table raises on any duplicate key
        filters = [a for a in acts if a.label.endswith("Filter settings…")]
        self.assertEqual(len(filters), 1, filters)
        self.assertEqual(filters[0].nav_target, "remote_filters")


if __name__ == "__main__":
    unittest.main()
