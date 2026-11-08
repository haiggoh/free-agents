#!/usr/bin/env python3
"""0.54.0 Task 2 (D3) — lowkey's API chooser: one collapsible group per provider with catalog
rows, an opt-in "Apply Remote-screen filters" toggle, and the session pickers' search bar.

Model-level tests run under any python; the two search tests drive the real picker and need
the picker venv (pytestmark load_sensitive, skip otherwise).
"""
import os
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import session_picker_model as m  # noqa: E402

from picker_harness import PilotCase  # noqa: E402  (imports fine without textual; setUp skips)

try:
    import textual  # noqa: F401
except ImportError:
    textual = None
sp = None
if textual is not None:
    import session_picker as sp  # noqa: E402

pytestmark = pytest.mark.load_sensitive


def agents():
    return [
        m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown", model_id="vendor/a"),
        m.RemoteAgent("nvidia-trial", "nvidia", "NVIDIA Trial", "trial", model_id="vendor/t"),
        m.RemoteAgent("gemini-f", "gemini", "Gemini F", "renewing_free", model_id="vendor/g"),
        m.RemoteAgent("hid-a", "hiddenprov", "Hidden A", "unknown", model_id="hp/a"),
    ]


def catalogs():
    return {
        "nvidia": [m.CatalogModel("vendor/a"), m.CatalogModel("vendor/cat-1"), m.CatalogModel("vendor/cat-2")],
        "gemini": [m.CatalogModel("vendor/g2")],
        "hiddenprov": [m.CatalogModel("hp/cat")],
    }


def make_screen(settings=None):
    s = settings or m.Settings()
    s.hidden_providers = ["hiddenprov"]
    lk = m.LowkeyScreen(s, [m.LocalModel(alias="qwen-3.8-operator", family="qwen")],
                        api_agents=agents())
    lk.set_api_catalogs(catalogs())
    return lk


def api_group(lk):
    return next(g for g in lk.groups() if g.id == "api")


def subgroups(g):
    # provider subgroups live as nested groups (the "local" group's family pattern)
    out = list(getattr(g, "groups", None) or [])
    if not out:
        out = [it for it in g.items if isinstance(it, m.Group)]
    return out


class ApiGroupStructureTests(unittest.TestCase):
    def test_api_section_has_one_group_per_provider(self):
        lk = make_screen()
        api = api_group(lk)
        subs = subgroups(api)
        titles = {g.id: g.label for g in subs}
        self.assertIn("nvidia", titles)
        self.assertIn("gemini", titles)
        self.assertNotIn("hiddenprov", titles)
        self.assertRegex(titles["nvidia"], r"^NVIDIA \(\d+\)$")

    def test_catalog_rows_appear_not_only_pinned(self):
        lk = make_screen()
        nvidia = next(g for g in subgroups(api_group(lk)) if g.id == "nvidia")
        ids = [i.id for i in nvidia.items]
        self.assertIn("cat:nvidia:vendor/cat-1", ids)
        self.assertIn("cat:nvidia:vendor/cat-2", ids)
        self.assertIn("nvidia:vendor/a", ids)          # pinned row present too

    def test_hidden_provider_absent_from_lowkey(self):
        lk = make_screen()
        ids = [i.id for g in subgroups(api_group(lk)) for i in g.items]
        self.assertFalse(any("hiddenprov" in i for i in ids))

    def test_no_duplicate_item_ids_in_lowkey(self):
        # pinned "vendor/a" and catalog "vendor/a" must yield ONE row
        lk = make_screen()
        api = api_group(lk)
        ids = [i.id for g in subgroups(api) for i in g.items]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate ids: {ids}")
        self.assertEqual(ids.count("nvidia:vendor/a") + ids.count("cat:nvidia:vendor/a"), 1)

    def test_rows_show_param_size_suffix(self):
        s = m.Settings()
        s.hidden_providers = ["hiddenprov"]
        s.model_param_count = lambda prov, mid: 123.0 if mid == "vendor/cat-1" else None
        lk = m.LowkeyScreen(s, [], api_agents=agents())
        lk.set_api_catalogs(catalogs())
        nvidia = next(g for g in subgroups(api_group(lk)) if g.id == "nvidia")
        row = next(i for i in nvidia.items if i.id == "cat:nvidia:vendor/cat-1")
        self.assertIn("· 123B", row.label, f"size suffix missing: {row.label!r}")

    def test_remote_filters_opt_in_toggle(self):
        saved = []
        s = m.Settings()
        s.hidden_providers = ["hiddenprov"]
        s.include_trials = False          # the Remote screen's trial filter hides vendor/t
        s.on_lowkey_filters_saved = lambda k, v: saved.append((k, v))
        lk = m.LowkeyScreen(s, [], api_agents=agents())
        lk.set_api_catalogs(catalogs())
        # default OFF: the trial row (which the Remote screen's filters hide) IS shown
        nvidia = next(g for g in subgroups(api_group(lk)) if g.id == "nvidia")
        self.assertIn("nvidia:vendor/t", [i.id for i in nvidia.items])
        self.assertFalse(lk._remote_filters)
        # toggle ON -> exactly the Remote screen's visible set for the same roster
        next(a for a in lk.actions() if a.key == "f").step(+1)   # 0.55.6 row step flips
        self.assertTrue(lk._remote_filters)
        self.assertTrue(s.lowkey_remote_filters)
        self.assertEqual(saved, [("remote_filters", True)])
        nvidia = next(g for g in subgroups(api_group(lk)) if g.id == "nvidia")
        shown = {i.id for g in subgroups(api_group(lk)) for i in g.items}
        remote_visible = {f"{a.provider}:{a.model_id or a.alias}"
                          for a in m.visible_agents(s, agents(), remote_filters=True)}
        pinned_shown = {i for i in shown if not i.startswith("cat:")}
        self.assertEqual(pinned_shown, remote_visible)
        self.assertNotIn("nvidia:vendor/t", shown)
        # keyless/hidden stay absent even with filters OFF (always-on rules)
        next(a for a in lk.actions() if a.key == "f").step(+1)   # 0.55.6 row step flips
        shown_off = {i.id for g in subgroups(api_group(lk)) for i in g.items}
        self.assertFalse(any("hiddenprov" in i for i in shown_off))


class SearchTests(unittest.TestCase):
    """Model-level: set_search filters rows and survives a late roster (set_models again)."""

    def test_search_filters_model_list(self):
        lk = make_screen()
        lk.set_search("cat-1")
        api = api_group(lk)
        ids = [i.id for g in subgroups(api) for i in g.items]
        self.assertEqual(ids, ["cat:nvidia:vendor/cat-1"])
        # group counts update: nvidia shows (1), gemini hidden (no match)
        subs = subgroups(api)
        self.assertEqual([g.id for g in subs], ["nvidia"])
        self.assertIn("(1)", subs[0].label)
        lk.set_search("")
        ids = [i.id for g in subgroups(api_group(lk)) for i in g.items]
        self.assertIn("nvidia:vendor/a", ids)
        self.assertIn("cat:gemini:vendor/g2", ids)

    def test_search_survives_late_roster(self):
        s = m.Settings()
        s.hidden_providers = ["hiddenprov"]
        lk = m.LowkeyScreen(s, [], api_agents=[])      # roster not landed yet
        lk.set_api_catalogs(catalogs())
        lk.set_search("gemini")
        # the roster lands late: set_models runs again through the sync — the filter persists
        lk.api_agents = agents()
        lk.set_models([])
        ids = [i.id for g in subgroups(api_group(lk)) for i in g.items]
        self.assertEqual(ids, ["gemini:vendor/g"])


@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class LowkeySearchPilotTests(PilotCase):
    """The #search bar on the lowkey screen — the session pickers' mechanism, driven for real."""

    def test_search_bar_filters_live(self):
        from picker_harness import REMOTE_INV
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])  # noqa: F811
        stub.write_text("#!/bin/sh\nfor a in \"$@\"; do [ \"$a\" = --inventory ] && { cat <<'EOF'\n"
                        + REMOTE_INV + "EOF\nexit 0; }; done\n")
        stub.chmod(0o755)

        async def scenario(app, pilot, h):
            sm = app.screen_model
            self.assertIsInstance(sm, m.LowkeyScreen)
            sm._toggle_choose_model()
            app.render_model()
            await h.wait_for(lambda: any(g.id == "api" and g.items for g in sm.accordion.groups),
                             "api rows", secs=15)
            # the search box is visible on the lowkey list (0.54.0 D3)
            search = app.query_one("#search")
            self.assertTrue(search.display)
            search.focus()
            await h.press("n", "v", "i", "d", "i", "a")   # types into the filter, narrows live
            await h.wait_for(lambda: all("nvidia" in i.id for g in subgroups(api_group(sm))
                                         for i in g.items) or not subgroups(api_group(sm)),
                             "filtered", secs=10)
            ids = [i.id for g in subgroups(api_group(sm)) for i in g.items]
            self.assertTrue(ids and all("nvidia" in i for i in ids), f"search did not filter: {ids}")
            sm.set_search("")
            app.render_model()
            await pilot.pause()
            ids2 = [i.id for g in subgroups(api_group(sm)) for i in g.items]
            self.assertTrue(any("gemini" in i for i in ids2), "clearing the search must restore all")

        self.run_picker(scenario, start="lowkey")


if __name__ == "__main__":
    unittest.main()
