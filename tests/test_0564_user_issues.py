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


if __name__ == "__main__":
    unittest.main()
