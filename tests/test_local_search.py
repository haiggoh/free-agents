#!/usr/bin/env python3
"""The live model filter on the LOCAL screen (user 2026-10-22: "the same search mechanic as
the Remote session list"). Same contract as tests/test_remote_search.py: a search keeps only
matching rows (case-insensitive substring of the row), opens EVERY group with a match, drops
groups without one, says so when nothing matches, and clearing restores the one-open
accordion. The "register new models" action header is an action, not a model, so a search
hides it; the unregistered models themselves still match inside their families.

Usage: python3 tests/test_local_search.py [-v]
Environment: none required (models are injected; no launcher is run).
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_picker_model as m  # noqa: E402


def models():
    return [m.LocalModel("qwen-3.8-operator", backend="rapid"),
            m.LocalModel("qwen-3.6-thinking", backend="rapid", thinking=True),
            m.LocalModel("deepseek-r1-architect", backend="rapid"),
            m.LocalModel("gemma-4-26b", backend="rapid"),
            m.LocalModel("glm-5-air", backend="omlx"),
            m.LocalModel("kat-v2.5-new", auto_scanned=True, folder="kat-v2.5-new")]


def item_ids(lane):
    return [obj.id for kind, obj in lane.accordion.rows() if kind == "item"]


def group_ids(lane):
    return [obj.id for kind, obj in lane.accordion.rows() if kind == "group"]


class LocalSearchTests(unittest.TestCase):
    def lane(self, missing=None):
        lane = m.LocalScreen(m.Settings())
        lane.set_models(models(), missing=missing)
        return lane

    def test_without_search_the_register_header_and_one_open_group(self):
        lane = self.lane()
        self.assertEqual(group_ids(lane)[0], m.NEW_MODELS_GROUP)
        self.assertIsNone(lane.accordion.multi_open)

    def test_search_keeps_only_matches_and_opens_every_matching_group(self):
        lane = self.lane()
        lane.set_search("QWEN")                       # case-insensitive
        self.assertEqual(sorted(item_ids(lane)), ["qwen-3.6-thinking", "qwen-3.8-operator"])
        self.assertNotIn(m.NEW_MODELS_GROUP, group_ids(lane))
        lane.set_search("e")                          # several families at once, all open
        opened = [g for g in group_ids(lane) if lane.accordion.is_open(g)]
        self.assertGreater(len(opened), 1)
        self.assertEqual(sorted(opened), sorted(group_ids(lane)))

    def test_search_matches_the_row_label_not_only_the_alias(self):
        lane = self.lane()
        lane.set_search("omlx")                       # the backend is part of the row
        self.assertEqual(item_ids(lane), ["glm-5-air"])

    def test_unregistered_and_missing_models_still_match(self):
        lane = self.lane(missing=["gone-model-7b"])
        lane.set_search("kat")
        self.assertEqual(item_ids(lane), ["kat-v2.5-new"])
        lane.set_search("gone")
        self.assertEqual(item_ids(lane), ["gone-model-7b"])

    def test_no_match_says_so(self):
        lane = self.lane()
        lane.set_search("zzz")
        self.assertEqual(group_ids(lane), ["__nomatch__"])
        self.assertIn('"zzz"', lane.accordion.groups[0].label)
        self.assertIn("matching", lane.policy)

    def test_clearing_restores_the_ordinary_list(self):
        lane = self.lane()
        before = group_ids(lane)
        lane.set_search("qwen")
        lane.set_search("")
        self.assertEqual(group_ids(lane), before)
        self.assertIsNone(lane.accordion.multi_open)
        self.assertNotIn("matching", lane.policy)

    def test_no_match_row_launches_nothing(self):
        lane = self.lane()
        lane.set_search("zzz")
        lane.accordion.select("__nomatch__:x")
        self.assertIsNone(lane.activate_selected())

    def test_launch_from_a_search_result(self):
        lane = self.lane()
        lane.set_search("architect")
        self.assertTrue(lane.accordion.select("deepseek-r1-architect"))
        req = lane.activate_selected()
        self.assertEqual(req.selected_id, "deepseek-r1-architect")



class LocalReadableNames(unittest.TestCase):
    """0.41.0 (user 2026-10-22): local rows read as names in the remote list's convention
    -- capitals and spaces, the maker first -- while the alias stays the id."""

    def lane(self, missing=None):
        lane = m.LocalScreen(m.Settings())
        lane.set_models(models(), missing=missing)
        return lane

    def labels(self, lane):
        lane.set_search("")
        for g in lane.accordion.groups:
            lane.accordion.expand(g.id)
        return {obj.id: obj.label for g in lane.accordion.groups for obj in g.items}

    def test_rows_read_as_names_but_keep_the_alias_as_id(self):
        lane = self.lane(missing=["gone-model-7b"])
        lab = {}
        for g in lane.accordion.groups:
            lab.update({i.id: i.label for i in g.items})
        self.assertTrue(lab["deepseek-r1-architect"].startswith("DeepSeek R1 Architect"), lab)
        self.assertTrue(lab["qwen-3.6-thinking"].startswith("Alibaba Qwen 3.6 Thinking"), lab)
        self.assertIn("Kwaipilot KAT V2.5 New", lab["kat-v2.5-new"])
        self.assertIn("Gone Model 7B", lab["gone-model-7b"])
        for label in lab.values():
            self.assertNotRegex(label.split("  ")[0], r"[a-z]-[a-z]", label)

    def test_search_still_matches_the_alias(self):
        lane = self.lane()
        lane.set_search("r1-arch")                     # what a user types from `csl` habit
        self.assertEqual(item_ids(lane), ["deepseek-r1-architect"])

    def test_info_box_shows_the_exact_alias(self):
        lane = self.lane(missing=["gone-model-7b"])
        text = lane.info_text("qwen-3.6-thinking")
        self.assertIn("Alibaba Qwen 3.6 Thinking", text)
        self.assertIn("Alias: qwen-3.6-thinking", text)
        self.assertIn("thinking", text.splitlines()[2])
        self.assertIn("Alias: gone-model-7b", lane.info_text("gone-model-7b"))
        self.assertIn("Highlight", lane.info_text(None))

    def test_go_launch_names_the_model(self):
        s = m.Settings()
        s.last_launched_model = {"local_session": "deepseek-r1-architect"}
        lane = m.LocalScreen(s)
        go = [a for a in lane.actions() if a.key == "g"][0]
        self.assertIn("Go launch: DeepSeek R1 Architect session", go.label)


if __name__ == "__main__":
    unittest.main()
