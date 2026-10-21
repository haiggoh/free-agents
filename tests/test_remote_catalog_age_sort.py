#!/usr/bin/env python3
"""The old-model filter and the per-provider size sort (T6, bin/session_picker_model).

The age filter hides catalog rows whose Hugging Face createdAt predates a cutoff
(LA_REMOTE_MAX_MODEL_AGE_MONTHS, default 18) until the picker toggle shows them; rows
with no date available stay visible, and pinned registry rows are never age-checked.
The sort orders each provider's rows largest-first, unknowns last — NVIDIA's pinned
rows keep their hand-curated order.

Usage: python3 tests/test_remote_catalog_age_sort.py [-v]
Environment: none required; no provider is called and no Hugging Face cache is read —
the size and age verdicts are injected as Settings callables, like catalog_local_capable.
"""
from pathlib import Path
import os
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_picker_model as m  # noqa: E402


def agents():
    return [m.RemoteAgent("mistral", "mistral", "Mistral (choose model)", "unknown", model_id="SELECT"),
            m.RemoteAgent("mistral-large", "mistral", "Mistral Large", "unknown",
                          model_id="mistral-large-latest"),
            m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown", model_id="vendor/aaa-9b"),
            m.RemoteAgent("nvidia-b", "nvidia", "NVIDIA B", "unknown", model_id="vendor/bbb-70b")]


def cat(*ids, tools=True):
    return [m.CatalogModel(i, tools) for i in ids]


class AgeFilterTests(unittest.TestCase):
    """Old catalog rows hide by default; the toggle (and --show-old's initial state) shows."""

    def screen(self, **settings):
        s = m.Settings()
        for k, v in settings.items():
            setattr(s, k, v)
        lane = m.RemoteScreen(s, agents(), owner=m.DIRECT_ROOT)
        lane._choose_model_visible = True
        return lane

    def ids(self, lane, group):
        return [i.id for g in lane.groups() if g.id == group for i in g.items]

    OLD = "mistral-old-7b"
    NEW = "mistral-fresh-7b"

    def load(self, lane):
        lane.accordion.expand("mistral")
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", cat(self.NEW, self.OLD), None)

    def test_old_rows_hidden_by_default_and_toggle_shows(self):
        lane = self.screen(catalog_model_old=lambda prov, mid: mid == self.OLD)
        self.load(lane)
        self.assertNotIn(f"cat:mistral:{self.OLD}", self.ids(lane, "mistral"))
        self.assertIn(f"cat:mistral:{self.NEW}", self.ids(lane, "mistral"))
        lane.settings.show_old = True
        lane._regroup()
        self.assertIn(f"cat:mistral:{self.OLD}", self.ids(lane, "mistral"))

    def test_show_old_setting_shows_from_the_start(self):
        # --show-old lands in Settings.show_old before the first paint (the picker's flags).
        lane = self.screen(show_old=True, catalog_model_old=lambda prov, mid: True)
        self.load(lane)
        self.assertIn(f"cat:mistral:{self.OLD}", self.ids(lane, "mistral"))

    def test_no_verdict_keeps_every_row_visible_fail_open(self):
        # The screen is built before any date is cached: with no verdict callable at all
        # (and with an explicit "unknown" False), nothing may hide.
        lane = self.screen()
        self.load(lane)
        self.assertIn(f"cat:mistral:{self.OLD}", self.ids(lane, "mistral"))
        lane = self.screen(catalog_model_old=lambda prov, mid: False)
        self.load(lane)
        self.assertIn(f"cat:mistral:{self.OLD}", self.ids(lane, "mistral"))

    def test_pinned_rows_are_never_age_hidden(self):
        # Pinned registry rows have no dates: even a verdict that calls every id old must
        # not hide them (the screen never asks about pinned rows).
        lane = self.screen(catalog_model_old=lambda prov, mid: True)
        self.assertIn("mistral-large", self.ids(lane, "mistral"))
        self.assertIn("nvidia-a", self.ids(lane, "nvidia"))

    def test_toggle_lives_in_the_filters_submenu(self):
        # Mirrors the unworking-models filter: a RemoteFiltersScreen action, not a key on
        # the remote screen itself (whose o is Temperature).
        lane = self.screen(catalog_model_old=lambda prov, mid: True)
        self.load(lane)
        filters = m.RemoteFiltersScreen(lane.settings, owner=m.DIRECT_ROOT)
        self.assertIn("o", {a.key for a in filters.actions()})
        self.assertNotIn(f"cat:mistral:{self.OLD}", self.ids(lane, "mistral"))
        filters._toggle("show_old")
        self.assertTrue(lane.settings.show_old)
        lane._regroup()
        self.assertIn(f"cat:mistral:{self.OLD}", self.ids(lane, "mistral"))
        filters._toggle("show_old")
        lane._regroup()
        self.assertNotIn(f"cat:mistral:{self.OLD}", self.ids(lane, "mistral"))


class AgeCutoffTests(unittest.TestCase):
    """The cutoff itself: env-tunable months, calendar-true, fail-open on bad input."""

    def tearDown(self):
        os.environ.pop(m.MAX_MODEL_AGE_MONTHS_ENV, None)

    def test_default_cutoff_is_18_months(self):
        os.environ.pop(m.MAX_MODEL_AGE_MONTHS_ENV, None)
        self.assertEqual(m.max_model_age_months(), 18)

    def test_env_cutoff_honored_and_bad_values_fall_back(self):
        os.environ[m.MAX_MODEL_AGE_MONTHS_ENV] = "6"
        self.assertEqual(m.max_model_age_months(), 6)
        os.environ[m.MAX_MODEL_AGE_MONTHS_ENV] = "nonsense"
        self.assertEqual(m.max_model_age_months(), 18)
        os.environ[m.MAX_MODEL_AGE_MONTHS_ENV] = "0"
        self.assertEqual(m.max_model_age_months(), 18)

    def test_created_before_cutoff(self):
        import datetime
        today = datetime.date(2026, 10, 21)
        self.assertFalse(m.created_before_cutoff("2025-08-04T22:33:06.000Z", 18, today))
        self.assertTrue(m.created_before_cutoff("2024-06-01T00:00:00.000Z", 18, today))
        # exactly on the cutoff is not older
        self.assertFalse(m.created_before_cutoff("2025-04-21T00:00:00.000Z", 18, today))
        # a future date, and anything unreadable, stays visible (fail-open)
        self.assertFalse(m.created_before_cutoff("2027-01-01T00:00:00.000Z", 18, today))
        self.assertFalse(m.created_before_cutoff("", 18, today))
        self.assertFalse(m.created_before_cutoff("not-a-date", 18, today))
        self.assertFalse(m.created_before_cutoff(None, 18, today))

    def test_cutoff_walks_back_over_year_ends_and_short_months(self):
        import datetime
        self.assertEqual(m.age_cutoff_date(1, datetime.date(2026, 3, 31)), datetime.date(2026, 2, 28))
        self.assertEqual(m.age_cutoff_date(12, datetime.date(2026, 3, 31)), datetime.date(2025, 3, 31))
        self.assertEqual(m.age_cutoff_date(4, datetime.date(2026, 2, 1)), datetime.date(2025, 10, 1))


class SizeSortTests(unittest.TestCase):
    """Per-provider size sort: largest first, unknowns last in their own order."""

    def screen(self, **settings):
        s = m.Settings()
        for k, v in settings.items():
            setattr(s, k, v)
        lane = m.RemoteScreen(s, agents(), owner=m.DIRECT_ROOT)
        lane._choose_model_visible = True
        return lane

    def ids(self, lane, group):
        return [i.id for g in lane.groups() if g.id == group for i in g.items]

    SIZES = {"big-70b": 70.0, "mid-13b": 13.0, "vendor/aaa-9b": 9.0, "vendor/bbb-70b": 70.0}

    def count(self, prov, mid):
        return self.SIZES.get(mid)

    def test_size_sorted_helper_descending_unknown_last(self):
        rows = ["b-5b", "none-1", "a-30b", "c-5b", "none-2"]
        sizes = {"b-5b": 5.0, "a-30b": 30.0, "c-5b": 5.0}
        out = m.size_sorted(rows, lambda r: sizes.get(r))
        self.assertEqual(out, ["a-30b", "b-5b", "c-5b", "none-1", "none-2"])

    def test_catalog_rows_sort_largest_first_unknown_last_in_catalog_order(self):
        lane = self.screen(model_param_count=self.count)
        lane.accordion.expand("mistral")
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", cat("small-3b", "unknown-1", "mid-13b", "big-70b", "unknown-2"), None)
        self.assertEqual(self.ids(lane, "mistral"),
                         ["mistral-large", "cat:mistral:big-70b", "cat:mistral:mid-13b",
                          "cat:mistral:small-3b", "cat:mistral:unknown-1", "cat:mistral:unknown-2"])

    def test_nvidia_pinned_rows_keep_their_manual_order(self):
        # The registry order is curated by hand (user 2026-10-20) — the 9B row listed
        # before the 70B row must stay there (fixture: nvidia-a IS the 9B row).
        self.assertEqual(agents()[2].model_id, "vendor/aaa-9b")
        lane = self.screen(model_param_count=self.count)
        self.assertEqual(self.ids(lane, "nvidia"), ["nvidia-a", "nvidia-b"])

    def test_non_nvidia_pinned_rows_sort_size_descending(self):
        pinned = [m.RemoteAgent("gemini-small", "gemini", "Gemini S", "unknown", model_id="g/s-9b"),
                  m.RemoteAgent("gemini-big", "gemini", "Gemini B", "unknown", model_id="g/b-70b")]
        sizes = {"g/s-9b": 9.0, "g/b-70b": 70.0}
        lane = m.RemoteScreen(m.Settings(model_param_count=lambda prov, mid: sizes.get(mid)),
                              agents() + pinned, owner=m.DIRECT_ROOT)
        lane._choose_model_visible = True
        self.assertEqual(self.ids(lane, "gemini"), ["gemini-big", "gemini-small"])

    def test_unsized_pinned_rows_sort_after_sized_in_inventory_order(self):
        pinned = [m.RemoteAgent("p1", "groq", "P1", "unknown", model_id="x/first-3b"),
                  m.RemoteAgent("p2", "groq", "P2", "unknown", model_id=""),
                  m.RemoteAgent("p3", "groq", "P3", "unknown", model_id="y/second-8b")]
        sizes = {"x/first-3b": 3.0, "y/second-8b": 8.0}
        lane = m.RemoteScreen(m.Settings(model_param_count=lambda prov, mid: sizes.get(mid)),
                              agents() + pinned, owner=m.DIRECT_ROOT)
        lane._choose_model_visible = True
        self.assertEqual(self.ids(lane, "groq"), ["p3", "p1", "p2"])

    def test_no_size_data_keeps_the_incoming_order(self):
        # Without the injected counter (a plain Settings()) nothing sorts: the catalog's
        # own order stands, so no test or screen ever reads the real Hugging Face cache.
        lane = self.screen()
        lane.accordion.expand("mistral")
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", cat("z-9b", "a-1b"), None)
        self.assertEqual(self.ids(lane, "mistral"),
                         ["mistral-large", "cat:mistral:z-9b", "cat:mistral:a-1b"])
        self.assertEqual(self.ids(lane, "nvidia"), ["nvidia-a", "nvidia-b"])

    def test_sort_is_display_only_ids_dedup_and_argv_unchanged(self):
        # The pinned row keeps its alias and its catalog duplicate stays deduped even
        # after sorting moved rows around; a pick still launches the exact model id.
        lane = self.screen(model_param_count=self.count)
        lane.accordion.expand("mistral")
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", cat("mistral-large-latest", "big-70b", "small-3b"), None)
        self.assertEqual(self.ids(lane, "mistral"),
                         ["mistral-large", "cat:mistral:big-70b", "cat:mistral:small-3b"])
        self.assertTrue(lane.accordion.select("cat:mistral:big-70b"))
        req = lane.activate_selected()
        self.assertEqual(req.argv[-3:], ["--remote-model", "big-70b", "mistral"])


if __name__ == "__main__":
    unittest.main()
