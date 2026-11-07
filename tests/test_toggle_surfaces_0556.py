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
from picker_harness import ROOT, PilotCase  # noqa: E402
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


def _action_option(h, key):
    """(option_index_in_list, plain prompt text) for the action row with this shortcut key."""
    from textual.widgets import OptionList
    ol = h.app.query_one("#actions", OptionList)
    idx = next(i for i, a in enumerate(h.app.action_ids) if a.key == key)
    opt_i = next(i for i in range(ol.option_count)
                 if ol.get_option_at_index(i).id == f"a:{idx}")
    return opt_i, str(ol.get_option_at_index(opt_i).prompt)


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


class RateLimiterRows(PilotCase):
    def _screen(self, **kw):
        s = m.Settings()
        for k, v in kw.items():
            setattr(s, k, v)
        calls = []
        scr = m.RateLimiterScreen(s, owner=m.DIRECT_ROOT, on_save=lambda d: calls.append(d))
        return s, scr, calls

    def test_rate_limiter_rows_are_steppers(self):
        s, scr, calls = self._screen(mode="smooth_bucket")
        acts = scr.actions()
        r = by_key(acts, "r")
        assert_stepper(self, r, 4)
        self.assertEqual(tuple(r.labels), ("10", "20", "30", "40"))
        self.assertEqual(tuple(by_key(acts, "w").labels), ("30s", "60s", "120s", "300s"))
        self.assertEqual(tuple(by_key(acts, "d").labels), ("5s", "10s", "30s", "60s"))
        self.assertEqual(tuple(by_key(acts, "u").labels), ("120s", "300s", "600s", "900s"))
        self.assertEqual(tuple(by_key(acts, "y").labels), ("×1.5", "×2.0", "×3.0"))
        self.assertEqual(tuple(by_key(acts, "z").labels), ("0", "3", "5", "10"))
        for k in ("o", "r", "w", "d", "u", "y", "z"):
            assert_stepper(self, by_key(acts, k))
        assert_stepper(self, by_key(acts, "c"), 4)     # bucket capacity in smooth_bucket mode
        _, scr2, _ = self._screen(mode="sliding_window")
        self.assertFalse(by_key(scr2.actions(), "c").enabled,
                         "capacity is smooth_bucket-only")
        self.assertIsNone(by_key(acts, "i").step)
        self.assertIsNone(by_key(acts, "x").step)

    def test_rate_limiter_left_steps_backward(self):
        s, scr, calls = self._screen(mode="smooth_bucket")
        s.rate_limiter["rpm"] = 40
        row = by_key(scr.actions(), "r")
        row.step(-1)
        self.assertEqual(s.rate_limiter["rpm"], 30, "Left must step BACKWARD")
        self.assertIn({"rpm": 30}, calls, "the step must persist via on_save")


class MemoryGuardRows(PilotCase):
    def _screen(self, on=True):
        s = m.Settings()
        calls = []
        scr = m.MemoryGuardScreen(s, owner=m.DIRECT_ROOT, on_save=lambda d: calls.append(d))
        if not on:
            s.memguard["enabled"] = False
        return s, scr, calls

    def test_memguard_rows_are_steppers(self):
        s, scr, calls = self._screen()
        acts = scr.actions()
        for k in ("g", "t", "c"):
            row = by_key(acts, k)
            assert_stepper(self, row, 2)
            self.assertEqual(tuple(row.labels), ("OFF", "ON"))
        for k in ("s", "h", "w"):
            assert_stepper(self, by_key(acts, k))
        # the plan's wording decision: the value sits at the END, after the last ": "
        self.assertIn("Compact idle sessions at RAM:", str(by_key(acts, "s").label))
        self.assertIn("Compact the largest session at once at RAM:", str(by_key(acts, "h").label))
        self.assertIn("Quiet time before the guard types into a session:", str(by_key(acts, "w").label))
        self.assertEqual(tuple(by_key(acts, "s").labels), ("≥75%", "≥80%", "≥85%", "≥90%"))
        self.assertEqual(tuple(by_key(acts, "w").labels), ("2s", "3s", "5s", "10s"))

    def test_memguard_pair_never_breaks_either_direction(self):
        s, scr, calls = self._screen()
        s.memguard["soft_pct"] = 75
        s.memguard["hard_pct"] = 90
        srow = by_key(scr.actions(), "s")
        hrow = by_key(scr.actions(), "h")
        for direction in (+1, -1):
            for _ in range(8):
                srow.step(direction)
                self.assertLess(s.memguard["soft_pct"], s.memguard["hard_pct"],
                                f"soft {s.memguard['soft_pct']} >= hard {s.memguard['hard_pct']} after s.step({direction})")
                hrow.step(direction)
                self.assertLess(s.memguard["soft_pct"], s.memguard["hard_pct"],
                                f"soft {s.memguard['soft_pct']} >= hard {s.memguard['hard_pct']} after h.step({direction})")

    def test_memguard_rows_inert_when_guard_off(self):
        s, scr, calls = self._screen(on=False)
        acts = scr.actions()
        for k in ("t", "s", "h", "w", "c"):
            self.assertFalse(by_key(acts, k).enabled, f"row {k} must be disabled with the guard OFF")
        self.assertTrue(by_key(acts, "g").enabled, "the master toggle stays usable")


class ThemeColumnAlignment(PilotCase):
    def seed(self):
        import shutil
        for name in ("local-theme.json", "remote-theme.json"):
            shutil.copy(ROOT / "config" / name, self.themes / name)

    def test_theme_rows_share_one_value_column(self):
        from rich.cells import cell_len
        async def s(app, pilot, h):
            self.seed()
            await h.press("h")
            await h.wait_screen("ThemeConfigScreen")
            cols_left, cols_right = set(), set()
            for key in ("l", "r", "y", "u"):
                text = str(_action_option(h, key)[1])
                cols_left.add(cell_len(text[:text.index("❮")]))
                cols_right.add(cell_len(text[:text.rindex("❯")]))
            self.assertEqual(len(cols_left), 1, f"❮ not in one column: {cols_left}")
            self.assertEqual(len(cols_right), 1, f"❯ not in one column: {cols_right}")
            # every value stays centered inside the shared cell
            for key in ("l", "r", "y", "u"):
                text = str(_action_option(h, key)[1])
                seg = text[text.index("❮") + 1:text.rindex("❯")]
                left = len(seg) - len(seg.lstrip(" "))
                right = len(seg) - len(seg.rstrip(" "))
                self.assertGreaterEqual(left, 1, text)
                self.assertGreaterEqual(right, 1, text)
                self.assertLessEqual(right, left + 1, text)
        self.run_picker(s)

    def test_other_screens_keep_per_row_width(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            widths = {}
            for key in ("t", "m"):
                text = str(_action_option(h, key)[1])
                widths[key] = text.rindex("❯") - text.index("❮") - 1
            self.assertNotEqual(widths["t"], widths["m"],
                                "other screens keep the 0.55.4 per-row widths")
        self.run_picker(s, start="local")
