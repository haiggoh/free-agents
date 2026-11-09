#!/usr/bin/env python3
"""tests/test_lowkey_keep_open_0541.py — 0.55.8 Task 3 (0541 plan Item B):

In Lowkey, an open provider/family group and the highlighted row survive a sync tick and a
warm-up apply (R16: BOTH nesting levels); a sync with an unchanged cache reports unchanged;
the FIRST "Choose model…" still starts collapsed.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_lowkey_keep_open_0541.py
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import PilotCase, ROOT  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, str(ROOT / "bin"))
try:
    import textual  # noqa: F401
except ImportError:
    textual = None
sp = None
if textual is not None:
    import session_picker as sp  # noqa: E402
import session_picker_model as m  # noqa: E402

pytestmark = pytest.mark.load_sensitive


class LowkeyKeepOpen(PilotCase):
    def _open_choose_model(self, app):
        sm = app.screen_model
        sm._toggle_choose_model()
        app.render_model()
        return sm

    def _local_models(self, n=3):
        return [m.LocalModel(alias=f"local-model-{i}", folder=f"/tmp/fake-{i}") for i in range(n)]

    def test_open_provider_group_survives_sync_tick(self):
        async def s(app, pilot, h):
            sm = self._open_choose_model(app)
            acc = sm.accordion
            api_group = next(g for g in acc.groups if g.id == "api")
            prov_group = next(i for i in api_group.items if isinstance(i, m.Group))
            acc.expand(prov_group.id)                       # nested provider group open
            first_item = prov_group.items[0]
            acc.select(first_item.id)                       # a row highlighted inside it
            # a sync tick with a CHANGED catalog: the groups rebuild
            sm.api_catalogs = {"nvidia": [{"id": first_item.id, "extra": 1}]}
            app._sync_screen_from_cache(sm)
            app.render_model()
            await pilot.pause(0.3)
            self.assertEqual(acc.open_group, prov_group.id,
                             f"the provider group must stay open: {acc.open_group}")
            self.assertEqual(acc.selected_id, first_item.id, "the highlight must survive")
            self.assertTrue(acc.is_open(prov_group.id) and acc.is_open("api"),
                            "BOTH levels stay open (R16)")
        self.run_picker(s, start="lowkey")

    def test_open_group_survives_warm_apply(self):
        async def s(app, pilot, h):
            sm = self._open_choose_model(app)
            acc = sm.accordion
            local_group = next(g for g in acc.groups if g.id == "local")
            acc.expand(local_group.id)
            family = next((i for i in local_group.items if isinstance(i, m.Group)), None)
            if family is not None:
                acc.expand(family.id)                       # nested family group open
            # the warm apply path (0.54.0 Task 3 hook) — now refresh_keep_open
            sm.refresh_keep_open()
            app.render_model()
            await pilot.pause(0.3)
            self.assertTrue(acc.is_open(local_group.id), "the local group must stay open")
            if family is not None:
                self.assertTrue(acc.is_open(family.id), "the nested family group must stay open")

    def test_open_group_survives_sync_with_search(self):
        # Review Focus 3: a search filter active with MULTIPLE matching groups — collapse()
        # clears multi_open in place, so the refresh must restore it or the extra groups close.
        async def s(app, pilot, h):
            sm = self._open_choose_model(app)
            acc = sm.accordion
            sm.api_catalogs = {"nvidia": [m.CatalogModel("org/x")],
                               "gemini": [m.CatalogModel("gemini/y")]}
            sm.search = "o"                                 # matches BOTH the api and local groups
            sm.refresh_keep_open()                          # first rebuild under the search
            acc.multi_open = {g.id for g in acc.groups if g.items}
            sm.refresh_keep_open()                          # the refresh under test
            app.render_model()
            await pilot.pause(0.3)
            self.assertEqual(sm.search, "o", "the filter must survive the refresh")
            open_now = {g.id for g in acc.groups if g.items and acc.is_open(g.id)}
            self.assertEqual(open_now, {g.id for g in acc.groups if g.items},
                             f"open={open_now} want={ {g.id for g in acc.groups if g.items} } "
                             f"multi={acc.multi_open} folded={acc.multi_open_folded} "
                             f"sel={acc.selected_id} og={acc.open_group}")
        self.run_picker(s, start="lowkey")

    def test_reorder_preserves_selection_by_id(self):
        # M3 F6: a rebuild that INSERTS a model (changing ordering) must not move the
        # selection — selection is by stable item id, not row position.
        async def s(app, pilot, h):
            sm = self._open_choose_model(app)
            acc = sm.accordion
            api_group = next(g for g in acc.groups if g.id == "api")
            prov_group = next(i for i in api_group.items if isinstance(i, m.Group))
            target = prov_group.items[0]
            acc.expand(prov_group.id)
            acc.select(target.id)
            new_catalog = {"nvidia": [m.CatalogModel("zzz-new-model")] +
                           [m.CatalogModel(i.id) for i in prov_group.items]}
            sm.api_catalogs = new_catalog
            app._sync_screen_from_cache(sm)
            self.assertEqual(acc.selected_id, target.id,
                             "the selection must follow the id, not the new position")
        self.run_picker(s, start="lowkey")

    def test_folded_state_restored_after_refresh(self):
        # M3 F1: multi_open_folded is restored alongside multi_open (a folded group stays folded).
        async def s(app, pilot, h):
            sm = self._open_choose_model(app)
            acc = sm.accordion
            sm.search = "o"
            sm.refresh_keep_open()
            folded_before = {"api"} if any(g.id == "api" for g in acc.groups) else set()
            acc.multi_open = {g.id for g in acc.groups if g.items} - folded_before
            acc.multi_open_folded = set(folded_before)
            sm.refresh_keep_open()
            self.assertEqual(acc.multi_open_folded, folded_before,
                             "the folded set must survive the refresh")
        self.run_picker(s, start="lowkey")

    def test_unchanged_sync_reports_unchanged(self):
        async def s(app, pilot, h):
            sm = self._open_choose_model(app)
            app._sync_screen_from_cache(sm)                 # first sync (changed=True)
            changed2 = app._sync_screen_from_cache(sm)      # identical cache
            self.assertFalse(changed2,
                             "an unchanged cache must report unchanged (U5: no spurious sync)")
        self.run_picker(s, start="lowkey")

    def test_first_choose_model_still_collapsed(self):
        async def s(app, pilot, h):
            sm = self._open_choose_model(app)
            self.assertFalse(sm.accordion.is_open("api") or sm.accordion.is_open("local"),
                             "the FIRST population must still start collapsed")
        self.run_picker(s, start="lowkey")


if __name__ == "__main__":
    unittest.main()
