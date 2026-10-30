#!/usr/bin/env python3
"""Live provider catalogs inside the remote accordion (bin/session_picker_model.RemoteScreen).

Usage: python3 tests/test_remote_catalog_accordion.py [-v]
Environment: none required; no provider is called (catalog rows are handed in).
"""
from pathlib import Path
import os
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_picker_model as m  # noqa: E402

CAT_CACHE_ENV = m.CATALOG_CACHE_DIR_ENV   # the override the model itself reads




# --- test hygiene: the catalog cache must never be the real one ---------------------------
# set_catalog() WRITES this cache, so a test that calls it without isolating the directory
# overwrites the user's real provider lists with fixture rows. Measured 2026-10-26: running this
# suite left cerebras.json holding exactly the `qwen-3.8-27b` fixture from this file, mistral.json
# holding 3 fixture rows, and groq/gemini 1 each — and the picker then painted those partial lists
# on first show, loading the real catalog only when the group was opened. That is the reported
# "nvidia starts with 9, gemini with 5" symptom; the caching itself works.

def _cat_cache_setup():
    import tempfile
    global _CAT_CACHE
    _CAT_CACHE = tempfile.mkdtemp(prefix="cat-cache-")
    os.environ[CAT_CACHE_ENV] = _CAT_CACHE

def _cat_cache_teardown():
    import shutil
    os.environ.pop(CAT_CACHE_ENV, None)
    shutil.rmtree(_CAT_CACHE, ignore_errors=True)

def setUpModule():
    _cat_cache_setup()

def tearDownModule():
    _cat_cache_teardown()


def agents():
    return [m.RemoteAgent("mistral", "mistral", "Mistral (choose model)", "unknown", model_id="SELECT"),
            m.RemoteAgent("mistral-large", "mistral", "Mistral Large", "unknown",
                          model_id="mistral-large-latest"),
            m.RemoteAgent("zai", "zai", "ZAI general API (choose model)", "unknown", model_id="SELECT"),
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
        self.assertEqual(lane.catalog_kind_needed(), "rcat:zai")   # pending: mistral never again
        self.assertIsNone(lane.catalog_kind_needed())              # (the placeholder drain ends)
        self.assertEqual(lane.accordion.open_group, "mistral")  # the loading regroup keeps it open

    def test_pinned_first_then_catalog_deduped(self):
        lane = self.screen()
        lane.accordion.expand("mistral")
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", cat("mistral-large-latest", "codestral-latest", "devstral-2"), None)
        self.assertEqual(self.ids(lane, "mistral"),
                         ["mistral-large", "cat:mistral:codestral-latest", "cat:mistral:devstral-2"])
        self.assertEqual(lane.catalog_kind_needed(), "rcat:zai")   # loaded: mistral never again
        self.assertIsNone(lane.catalog_kind_needed())              # (the placeholder drain ends)
        self.assertEqual(lane.accordion.open_group, "mistral")

    def test_choose_model_placeholder_row_is_gone(self):
        # The "(choose model)" row is not a launchable model: the provider's roster IS its
        # catalog, so the placeholder row disappears and real pinned rows stay (T1, 2026-10-20).
        lane = self.screen()
        lane.accordion.expand("mistral")
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", cat("codestral-latest"), None)
        self.assertNotIn("mistral", self.ids(lane, "mistral"))
        self.assertIn("mistral-large", self.ids(lane, "mistral"))

    def test_placeholder_only_provider_still_gets_a_group(self):
        # zai has ONLY the placeholder row: the group must survive on the catalog's back.
        lane = self.screen()
        lane.accordion.expand("zai")
        self.assertEqual(lane.catalog_kind_needed(), "rcat:zai")
        self.assertIn("cat:zai:__loading__", self.ids(lane, "zai"))
        lane.set_catalog("zai", cat("glm-5.3-flash", "glm-5"), None)
        self.assertEqual(self.ids(lane, "zai"), ["cat:zai:glm-5.3-flash", "cat:zai:glm-5"])

    def test_placeholder_only_catalog_loads_without_expanding(self):
        # The placeholder's catalog IS the roster, so it loads as soon as the model list is
        # shown — one provider per tick, after the open group — without waiting for anyone
        # to open zai. (The accordion auto-opens the first group, mistral: served first.)
        lane = self.screen()
        self.assertEqual(lane.catalog_kind_needed(), "rcat:mistral")   # open group first
        self.assertEqual(lane.catalog_kind_needed(), "rcat:zai")       # then the placeholder
        self.assertIn("cat:zai:__loading__", self.ids(lane, "zai"))
        self.assertIsNone(lane.catalog_kind_needed())          # pending: never asked twice
        self.assertEqual(lane.accordion.open_group, "mistral")  # the drain keeps it open

    def test_catalog_launch_on_placeholder_only_provider(self):
        # Selecting a catalog row launches through the placeholder alias with --remote-model.
        lane = self.screen()
        lane.accordion.expand("zai")
        lane.catalog_kind_needed()
        lane.set_catalog("zai", cat("glm-5.3-flash"), None)
        self.assertTrue(lane.accordion.select("cat:zai:glm-5.3-flash"))
        req = lane.activate_selected()
        self.assertEqual(req.argv[-3:], ["--remote-model", "glm-5.3-flash", "zai"])

    def test_placeholder_not_counted_as_visible_or_hidden(self):
        # The placeholder is replaced, not hidden: neither count may include it.
        lane = self.screen(include_trials=False)   # cerebras (trial) is the one hidden row
        self.assertEqual(lane.policy, "2 model(s) visible  (hidden: 1)")

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

    def test_tools_untested_label_is_gone(self):
        # The per-row "tools untested" suffix is stale noise on nearly every row (user
        # 2026-10-20): gone from catalog rows and pinned rows alike.
        lane = self.screen()
        lane.accordion.expand("gemini")
        lane.catalog_kind_needed()
        lane.set_catalog("gemini-flash", cat("gemini-x", tools="unknown"), None)
        label = next(i.label for g in lane.groups() for i in g.items if i.id == "cat:gemini:gemini-x")
        self.assertNotIn("tools untested", label)
        for g in lane.groups():
            for i in g.items:
                self.assertNotIn("tools untested", i.label)

    def lane_screen(self, agents):
        s = m.Settings()
        lane = m.RemoteScreen(s, agents, owner=m.DIRECT_ROOT)
        lane._choose_model_visible = True
        return lane

    def route_cache_env(self, verdicts):
        import json as js
        import tempfile
        cache = Path(tempfile.mkdtemp()) / "route.json"
        os.environ["LA_ANTHROPIC_ROUTE_CACHE"] = str(cache)
        self.addCleanup(os.environ.pop, "LA_ANTHROPIC_ROUTE_CACHE", None)
        cache.write_text(js.dumps(verdicts))

    def test_lane_tags_on_rows_follow_the_route_cache(self):
        # Lane = native Anthropic / chat/completions / undecided (not yet probed). Rows tag
        # only non-default lanes; providers without anthropic_base are always chat and
        # never tagged. Verdicts come from anthropic-route's own cache file (env-redirected).
        self.route_cache_env({
            "zai|glm-native": {"route": "anthropic", "at": 1, "status": 200},
            "kilo|kilo-chat": {"route": "chat", "at": 1, "status": 400}})
        agents = [m.RemoteAgent("zai", "zai", "ZAI", "unknown", model_id="SELECT"),
                  m.RemoteAgent("kilo-a", "kilo", "Kilo A", "unknown", model_id="kilo-chat"),
                  m.RemoteAgent("gemini-flash", "gemini", "Gemini Flash", "renewing_free",
                                model_id="gemini-x")]
        lane = self.lane_screen(agents)
        lane.accordion.expand("zai")
        lane.catalog_kind_needed()
        lane.set_catalog("zai", cat("glm-native", "glm-unprobed"), None)
        labels = {i.id: i.label for g in lane.groups() for i in g.items}
        self.assertIn("native Anthropic", labels["cat:zai:glm-native"])
        self.assertIn("undecided (not yet probed)", labels["cat:zai:glm-unprobed"])
        self.assertNotIn("Anthropic", labels["kilo-a"])     # cached chat = the default, no tag
        self.assertNotIn("Anthropic", labels["gemini-flash"])  # no anthropic_base, no tag

    def test_catalog_info_text_shows_lane_and_drops_boilerplate(self):
        self.route_cache_env({"zai|glm-native": {"route": "anthropic", "at": 1, "status": 200}})
        agents = [m.RemoteAgent("zai", "zai", "ZAI", "unknown", model_id="SELECT")]
        lane = self.lane_screen(agents)
        text = lane.info_text("cat:zai:glm-native")
        # 0.48.0: the "Lane:" line is gone from infoboxes (user 2026-10-30 — no decision value);
        # the alias + provider name still lead the box.
        self.assertNotIn("Lane:", text)
        self.assertIn("Z.ai catalog", text)
        self.assertNotIn("untested", text)
        self.assertNotIn("Listed by", text)
        self.assertNotIn("not pinned", text)

    def test_catalog_line_parse(self):
        c = m.CatalogModel.from_line('a/b\t{"pricing": {"prompt": "0"}, "tier": "unknown", "tools": true}')
        self.assertEqual((c.model_id, c.tools, c.pricing), ("a/b", True, {"prompt": "0"}))
        self.assertEqual(m.CatalogModel.from_line("a/b\tnot json").tools, "unknown")


class CatalogStaleIterationTests(unittest.TestCase):
    """A stale provider with no visible anchor must not crash the render.

    Reproduces the user-reported crash (2026-10-25, free-agents 0.44.1): typing in the live
    search while the provider catalogs are still loading raised
        RuntimeError: Set changed size during iteration
    from catalog_kind_needed(). The cause is in this file's own loop: it iterates
    `self._catalog_stale` and DISCARDS from that same set (the `anchor is None` branch), so the
    set changes size mid-iteration and the next `__next__` raises. The render path calls this on
    every keystroke, so any re-render landing mid-load crashed the whole picker.
    """

    def screen(self):
        s = m.Settings()
        lane = m.RemoteScreen(s, agents(), owner=m.DIRECT_ROOT)
        lane._choose_model_visible = True
        return lane

    def test_a_stale_provider_with_no_anchor_does_not_raise(self):
        lane = self.screen()
        lane.catalogs.clear()
        # Every real provider is in flight, so the earlier loops in catalog_kind_needed fire
        # nothing and we reach the stale loop; 'groq' has no agent at all.
        lane._catalog_pending = {"mistral", "zai", "gemini", "cerebras"}
        lane._catalog_stale = {"groq", "mistral2"}     # a second entry AFTER the one discarded
        try:
            lane.catalog_kind_needed()
        except RuntimeError as exc:
            self.fail(f"catalog_kind_needed raised while pruning stale providers: {exc}")
        # ...and the dead provider is actually pruned, rather than left to be retried forever.
        self.assertNotIn("groq", lane._catalog_stale)


if __name__ == "__main__":
    unittest.main()


