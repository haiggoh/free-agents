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
        # Review Focus 3: a provider group open AND a search filter active
        async def s(app, pilot, h):
            sm = self._open_choose_model(app)
            acc = sm.accordion
            sm.search = "local"                             # the live filter text
            acc.multi_open = {g.id for g in acc.groups}     # search mode: all groups open
            sm.refresh_keep_open()
            app.render_model()
            await pilot.pause(0.3)
            self.assertEqual(sm.search, "local", "the filter must survive the refresh")
            self.assertTrue(all(acc.is_open(g.id) for g in acc.groups if g.items),
                            "search-mode multi-open must survive the refresh")
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
