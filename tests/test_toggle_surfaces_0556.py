#!/usr/bin/env python3
"""tests/test_toggle_surfaces_0556.py — 0.55.6 Tasks 2-5: every setting surface renders the
cycle-glyph design. Model level: each converted row is a STEPPER (step + labels), not a
plain action — the render draws "KEY) name: <value>" only for steppers.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_toggle_surfaces_0556.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import PilotCase  # noqa: E402
import pytest  # noqa: E402

import session_picker_model as m  # noqa: E402

pytestmark = pytest.mark.load_sensitive


def assert_stepper(t, action, n_labels=None):
    """The row carries the cycle design: a step callable + display labels for its values."""
    t.assertIsNotNone(action.step, f"row {action.key!r} has no step — renders bare")
    t.assertTrue(action.labels, f"row {action.key!r} has no labels")
    if n_labels is not None:
        t.assertEqual(len(action.labels), n_labels)


def by_key(acts, k):
    return next(a for a in acts if a.key == k)


class LocalAndLowkeyRows(PilotCase):
    def test_local_autosync_is_a_toggle(self):
        s = m.Settings()
        calls = []
        s.on_memguard_saved = lambda d: calls.append(d)
        scr = m.LocalScreen(s, [], owner=m.HOME_OWNED)
        row = by_key(scr.actions(), "y")
        assert_stepper(self, row, 2)
        self.assertEqual(tuple(row.labels), ("OFF", "ON"))
        before = bool(s.memguard.get("registry_auto_sync"))
        row.step(+1)
        self.assertEqual(bool(s.memguard.get("registry_auto_sync")), not before,
                         "step(+1) must flip the store key")
        self.assertEqual(calls, [{"registry_auto_sync": not before}],
                         "the on_memguard_saved callback must persist the flip")

    def test_lowkey_effort_uses_short_phrasing(self):
        scr = m.LowkeyScreen(m.Settings(), [], api_agents=[])
        row = by_key(scr.actions(), "e")
        self.assertNotIn("sent as", str(row.label), row.label)
        joined = " ".join(row.labels)
        self.assertNotIn("sent as", joined, joined)
        self.assertEqual(tuple(row.labels),
                         tuple(m._effort_label(c) for c in m.effort_choices("lowkey")))

    def test_lowkey_remote_filters_is_a_toggle(self):
        s = m.Settings()
        scr = m.LowkeyScreen(s, [], api_agents=[])
        row = by_key(scr.actions(), "f")
        assert_stepper(self, row, 2)
        self.assertEqual(tuple(row.labels), ("OFF", "ON"))
        before = scr._remote_filters
        row.step(-1)
        self.assertEqual(scr._remote_filters, not before)
        self.assertEqual(bool(s.lowkey_remote_filters), not before,
                         "the put must persist like the old toggle did")
        row.step(+1)
        self.assertEqual(scr._remote_filters, before)



class RemoteFiltersRows(PilotCase):
    def test_remote_filters_rows_are_toggles(self):
        scr = m.RemoteFiltersScreen(m.Settings(), owner=m.DIRECT_ROOT)
        acts = scr.actions()
        for k in ("h", "f", "u", "o"):
            row = by_key(acts, k)
            assert_stepper(self, row, 2)
            self.assertEqual(tuple(row.labels), ("HIDDEN", "SHOWN"))
        self.assertIsNone(by_key(acts, "x").step, "the report row stays a plain nav tool")
        # direction: a filter row toggles both ways and persists on the settings object
        row = by_key(acts, "h")
        before = scr.settings.include_trials
        row.step(+1)
        self.assertEqual(scr.settings.include_trials, not before)
        row.step(-1)
        self.assertEqual(scr.settings.include_trials, before)


class CloudConfigRows(PilotCase):
    def _screen(self, **kw):
        s = m.Settings()
        for k, v in kw.items():
            setattr(s, k, v)
        return s, m.CloudConfigScreen(s, owner=m.DIRECT_ROOT)

    def test_cloud_rows_are_steppers(self):
        import cloud_session_env as cse
        s, scr = self._screen(security_review_offered=True)
        acts = scr.actions()
        x = by_key(acts, "x")
        assert_stepper(self, x, len(cse.ROUTE_MODES))
        self.assertEqual(tuple(x.labels), tuple(cse.ROUTE_LABELS[k] for k in cse.ROUTE_MODES))
        assert_stepper(self, by_key(acts, "p"), 2)
        assert_stepper(self, by_key(acts, "d"), 2)
        assert_stepper(self, by_key(acts, "s"))
        self.assertIsNone(by_key(acts, "e").step, "the probe row stays a plain tool")

    def test_cloud_left_steps_backward(self):
        import cloud_session_env as cse
        calls = []
        s, scr = self._screen()
        s.on_cloud_saved = lambda d: calls.append(d)
        row = by_key(scr.actions(), "x")
        start = s.routes.get("agent", "free-first")
        row.step(+1)
        mid = s.routes["agent"]
        self.assertNotEqual(mid, start)
        self.assertIn({"route": ("agent", mid)}, calls, "every step must persist")
        row.step(-1)
        self.assertEqual(s.routes["agent"], start, "Left must step BACKWARD")
        self.assertIn({"route": ("agent", start)}, calls)

    def test_bypass_note_is_outside_the_value(self):
        s, scr = self._screen()
        row = by_key(scr.actions(), "p")
        self.assertNotIn(m.NOT_GATEWAY_NOTE, " ".join(row.labels))
        self.assertIn(m.NOT_GATEWAY_NOTE, str(row.value_suffix))

    def test_default_mode_row_inert_when_policy_blocks(self):
        import cloud_session_env as cse
        s, scr = self._screen()
        saved = cse.bypass_blocked_by_policy
        cse.bypass_blocked_by_policy = lambda: "managed policy"
        self.addCleanup(setattr, cse, "bypass_blocked_by_policy", saved)
        row = by_key(scr.actions(), "d")
        self.assertFalse(row.enabled)
        before = s.default_mode
        if row.step is not None:
            row.step(+1)
        self.assertEqual(s.default_mode, before, "a managed policy must not be stepped around")


if __name__ == "__main__":
    unittest.main()
