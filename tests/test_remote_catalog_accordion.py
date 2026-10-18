#!/usr/bin/env python3
"""Live provider catalogs inside the remote accordion (bin/session_picker_model.RemoteScreen).

Usage: python3 tests/test_remote_catalog_accordion.py [-v]
Environment: none required; no provider is called (catalog rows are handed in).
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_picker_model as m  # noqa: E402


def agents():
    return [m.RemoteAgent("mistral", "mistral", "Mistral (choose model)", "unknown", model_id="SELECT"),
            m.RemoteAgent("mistral-large", "mistral", "Mistral Large", "unknown",
                          model_id="mistral-large-latest"),
            m.RemoteAgent("gemini-flash", "gemini", "Gemini Flash", "renewing_free",
                          model_id="gemini-3.8-flash"),
            m.RemoteAgent("cerebras-oss", "cerebras", "Cerebras OSS", "trial", model_id="gpt-oss-120b")]


def cat(*ids, tools=True):
    return [m.CatalogModel(i, tools) for i in ids]


class CatalogAccordionTests(unittest.TestCase):
    def screen(self, **settings):
        s = m.Settings()
        for k, v in settings.items():
            setattr(s, k, v)
        lane = m.RemoteScreen(s, agents(), owner=m.DIRECT_ROOT)
        lane._choose_model_visible = True
        return lane

    def ids(self, lane, group):
        return [i.id for g in lane.groups() if g.id == group for i in g.items]

    def test_expanding_a_group_asks_once_and_shows_loading(self):
        lane = self.screen()
        lane.accordion.expand("mistral")
        self.assertEqual(lane.catalog_kind_needed(), "rcat:mistral")
        self.assertIn("cat:mistral:__loading__", self.ids(lane, "mistral"))
        self.assertIsNone(lane.catalog_kind_needed())          # pending: never asked twice
        self.assertEqual(lane.accordion.open_group, "mistral")  # the loading regroup keeps it open

    def test_pinned_first_then_catalog_deduped(self):
        lane = self.screen()
        lane.accordion.expand("mistral")
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", cat("mistral-large-latest", "codestral-latest", "devstral-2"), None)
        self.assertEqual(self.ids(lane, "mistral"),
                         ["mistral", "mistral-large", "cat:mistral:codestral-latest", "cat:mistral:devstral-2"])
        self.assertIsNone(lane.catalog_kind_needed())          # loaded: not fetched again
        self.assertEqual(lane.accordion.open_group, "mistral")

    def test_error_row_instead_of_empty_group(self):
        lane = self.screen()
        lane.accordion.expand("mistral")
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", [], "rcat:mistral model list failed to load: HTTP 401")
        ids = self.ids(lane, "mistral")
        self.assertIn("cat:mistral:__error__", ids)
        label = next(i.label for g in lane.groups() for i in g.items if i.id == "cat:mistral:__error__")
        self.assertIn("HTTP 401", label)
        self.assertIsNone(lane.activate_selected())

    def test_launch_catalog_row_uses_remote_model_and_is_remembered(self):
        remembered = []
        lane = self.screen(on_last_launched=lambda lane_, alias, tier: remembered.append(alias))
        lane.accordion.expand("mistral")
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", cat("codestral-latest"), None)
        self.assertTrue(lane.accordion.select("cat:mistral:codestral-latest"))
        req = lane.activate_selected()
        self.assertEqual(req.argv[-3:], ["--remote-model", "codestral-latest", "mistral"])
        self.assertEqual(remembered, ["mistral@codestral-latest"])
        again = lane._launch_last("remote_api_session")
        self.assertEqual(again.argv[-3:], ["--remote-model", "codestral-latest", "mistral"])

    def test_pinned_row_launch_unchanged(self):
        lane = self.screen()
        lane.accordion.select("mistral-large")
        self.assertNotIn("--remote-model", lane.activate_selected().argv)

    def test_catalog_rows_obey_local_capable_filter(self):
        lane = self.screen(catalog_local_capable=lambda prov, mid: mid == "devstral-small")
        lane.accordion.expand("mistral")
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", cat("devstral-small", "mistral-medium"), None)
        ids = self.ids(lane, "mistral")
        self.assertNotIn("cat:mistral:devstral-small", ids)
        self.assertIn("cat:mistral:mistral-medium", ids)
        lane.settings.local_capable_shown = True
        lane._regroup()
        self.assertIn("cat:mistral:devstral-small", self.ids(lane, "mistral"))

    def test_trial_catalog_rows_follow_the_trial_filter(self):
        lane = self.screen()
        lane.accordion.expand("cerebras")
        self.assertEqual(lane.catalog_kind_needed(), "rcat:cerebras-oss")
        lane.set_catalog("cerebras-oss", cat("qwen-3.8-27b"), None)
        self.assertIn("cat:cerebras:qwen-3.8-27b", self.ids(lane, "cerebras"))
        lane.accordion.select("cat:cerebras:qwen-3.8-27b")
        self.assertIn("--include-trials", lane.activate_selected().argv)
        lane.settings.include_trials = False
        lane._regroup()
        self.assertNotIn("cerebras", [g.id for g in lane.groups()])

    def test_untested_tools_are_labelled(self):
        lane = self.screen()
        lane.accordion.expand("gemini")
        lane.catalog_kind_needed()
        lane.set_catalog("gemini-flash", cat("gemini-x", tools="unknown"), None)
        label = next(i.label for g in lane.groups() for i in g.items if i.id == "cat:gemini:gemini-x")
        self.assertIn("tools untested", label)

    def test_catalog_line_parse(self):
        c = m.CatalogModel.from_line('a/b\t{"pricing": {"prompt": "0"}, "tier": "unknown", "tools": true}')
        self.assertEqual((c.model_id, c.tools, c.pricing), ("a/b", True, {"prompt": "0"}))
        self.assertEqual(m.CatalogModel.from_line("a/b\tnot json").tools, "unknown")


if __name__ == "__main__":
    unittest.main()
