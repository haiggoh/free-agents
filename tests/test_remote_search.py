#!/usr/bin/env python3
"""Model-name search on the Remote screen (T7, bin/session_picker_model).

A search narrows every provider group to the rows whose model matches (case-insensitive
substring of the id or display name) and, as the one exception to the accordion's
one-open-group rule, opens EVERY group that has a match at once — a model offered by
several providers shows under each. Groups with no match disappear; clearing the search
restores the normal single-open accordion. Searching also loads every provider's catalog,
since a catalog otherwise loads only when its group is opened.

Usage: python3 tests/test_remote_search.py [-v]
Environment: none required; no provider is called (catalogs are injected).
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_picker_model as m  # noqa: E402


def agents():
    return [m.RemoteAgent("nvidia-glm", "nvidia", "NVIDIA GLM 5.3 Flash", "unknown",
                          model_id="z-ai/glm-5.3-flash"),
            m.RemoteAgent("nvidia-kimi", "nvidia", "NVIDIA Kimi", "unknown", model_id="moonshot/kimi-k3"),
            m.RemoteAgent("zai-glm", "zai", "Z.ai GLM 5.3", "unknown", model_id="glm-5.3"),
            m.RemoteAgent("groq-llama", "groq", "Groq Llama", "unknown", model_id="llama-4-70b")]


def item_ids(lane):
    return [obj.id for kind, obj in lane.accordion.rows() if kind == "item"]


def group_ids(lane):
    return [obj.id for kind, obj in lane.accordion.rows() if kind == "group"]


class SearchTests(unittest.TestCase):
    def lane(self):
        return m.RemoteScreen(m.Settings(), agents())

    def test_without_search_only_one_group_is_open(self):
        lane = self.lane()
        self.assertEqual(item_ids(lane), ["nvidia-glm", "nvidia-kimi"])

    def test_search_opens_every_matching_group_with_only_matching_rows(self):
        lane = self.lane()
        lane.set_search("GLM")                       # case-insensitive
        self.assertEqual(group_ids(lane), ["nvidia", "zai"])        # groq has no match: gone
        self.assertEqual(item_ids(lane), ["nvidia-glm", "zai-glm"])  # kimi filtered out

    def test_search_matches_catalog_rows_too(self):
        lane = self.lane()
        lane.set_catalog("groq-llama", [m.CatalogModel("glm-4.5-air", True),
                                        m.CatalogModel("mixtral-8x7b", True)], None)
        lane.set_search("glm")
        self.assertIn("groq", group_ids(lane))
        self.assertIn(f"{m.CATALOG_PREFIX}groq:glm-4.5-air", item_ids(lane))
        self.assertNotIn(f"{m.CATALOG_PREFIX}groq:mixtral-8x7b", item_ids(lane))

    def test_matches_display_name_as_well_as_id(self):
        lane = self.lane()
        lane.set_search("kimi")
        self.assertEqual(item_ids(lane), ["nvidia-kimi"])
        lane.set_search("moonshot/")
        self.assertEqual(item_ids(lane), ["nvidia-kimi"])

    def test_clearing_restores_single_open_accordion(self):
        lane = self.lane()
        lane.set_search("glm")
        lane.set_search("")
        self.assertEqual(group_ids(lane), ["nvidia", "zai", "groq"])
        self.assertEqual(item_ids(lane), ["nvidia-glm", "nvidia-kimi"])

    def test_header_toggle_during_search_folds_only_that_group(self):
        lane = self.lane()
        lane.set_search("glm")
        lane.accordion.collapse("nvidia")
        self.assertEqual(item_ids(lane), ["zai-glm"])
        lane.accordion.expand("nvidia")
        self.assertEqual(item_ids(lane), ["nvidia-glm", "zai-glm"])

    def test_search_survives_a_catalog_refresh(self):
        lane = self.lane()
        lane.set_search("glm")
        lane.set_catalog("groq-llama", [m.CatalogModel("glm-4.5-air", True)], None)
        self.assertEqual(group_ids(lane), ["nvidia", "zai", "groq"])
        self.assertTrue(all(lane.accordion.is_open(g) for g in group_ids(lane)))

    def test_search_loads_every_providers_catalog(self):
        lane = self.lane()
        lane.set_search("glm")
        kinds = set()
        while (k := lane.catalog_kind_needed()) is not None:
            kinds.add(k)
            alias = k.split(":", 1)[1]
            lane.set_catalog(alias, [], None)
        self.assertEqual({k.split(":", 1)[1] for k in kinds}, {"nvidia-glm", "zai-glm", "groq-llama"})

    def test_no_match_says_so(self):
        lane = self.lane()
        lane.set_search("does-not-exist")
        self.assertEqual(item_ids(lane), ["__nomatch__:x"])
        self.assertIn("does-not-exist", lane.accordion.groups[0].label)

    def test_find_action_and_policy_line(self):
        lane = self.lane()
        find = next(a for a in lane.actions() if a.key == "w")
        self.assertEqual(find.run(), m.Nav("prompt:search"))
        lane.set_search("glm")
        self.assertIn("glm", next(a for a in lane.actions() if a.key == "w").label)
        self.assertIn("matching", lane.policy)

    def test_filters_still_apply_under_search(self):
        lane = self.lane()
        lane.agents[2].broken = True                  # zai-glm marked unworking
        lane.set_search("glm")
        self.assertEqual(item_ids(lane), ["nvidia-glm"])

    def test_launch_from_a_search_result_is_unchanged(self):
        lane = self.lane()
        lane.set_search("glm")
        self.assertTrue(lane.accordion.select("zai-glm"))
        self.assertEqual(lane.activate_selected().argv[-1], "zai-glm")


class AccordionMultiOpenTests(unittest.TestCase):
    def test_default_accordion_is_single_open(self):
        acc = m.Accordion()
        acc.set_groups([m.Group("a", "A", [m.Item("a1", "x")]), m.Group("b", "B", [m.Item("b1", "y")])])
        acc.expand("b")
        self.assertFalse(acc.is_open("a"))
        self.assertTrue(acc.is_open("b"))
        acc.collapse()
        self.assertFalse(acc.is_open("b"))


if __name__ == "__main__":
    unittest.main()
