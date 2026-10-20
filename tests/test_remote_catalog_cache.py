#!/usr/bin/env python3
"""Disk-cached provider catalogs (bin/session_picker_model.RemoteScreen, 0.38.2 work).

The picker renders a provider's catalog from a per-provider cache instantly and revises
it in the background once per session; a failed refresh keeps the cached list and says so.

Usage: python3 tests/test_remote_catalog_cache.py [-v]
Environment: none required; the cache dir is redirected to a temp dir per test
(LA_REMOTE_CATALOG_CACHE_DIR) and no provider is called.
"""
from pathlib import Path
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_picker_model as m  # noqa: E402


def cat(*ids, tools=True):
    return [m.CatalogModel(i, tools) for i in ids]


class CatalogCacheStoreTests(unittest.TestCase):
    """The cache file itself: round-trip, modes, corruption tolerance."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fa-cat-cache-test-")
        os.environ[m.CATALOG_CACHE_DIR_ENV] = self.tmp

    def tearDown(self):
        os.environ.pop(m.CATALOG_CACHE_DIR_ENV, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_then_read_roundtrips_rows(self):
        rows = [m.CatalogModel("glm-5.3-flash", True), m.CatalogModel("glm-5", "unknown")]
        m.write_catalog_cache("zai", rows)
        back = m.read_catalog_cache("zai")
        self.assertEqual([r.model_id for r in back], ["glm-5.3-flash", "glm-5"])
        self.assertIs(back[0].tools, True)
        self.assertEqual(back[1].tools, "unknown")

    def test_cache_file_is_600_and_dir_is_700(self):
        m.write_catalog_cache("zai", cat("glm-5.3-flash"))
        d = m.catalog_cache_dir()
        p = d / "zai.json"
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700, "cache dir must be 700")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, "cache file must be 600")

    def test_write_is_atomic_no_tmp_left_behind(self):
        m.write_catalog_cache("zai", cat("glm-5.3-flash"))
        self.assertEqual([p.name for p in m.catalog_cache_dir().iterdir()], ["zai.json"])

    def test_missing_or_corrupt_cache_reads_none(self):
        self.assertIsNone(m.read_catalog_cache("never-fetched"))
        (m.catalog_cache_dir().mkdir(parents=True, exist_ok=True))
        (m.catalog_cache_dir() / "zai.json").write_text("{not json")
        self.assertIsNone(m.read_catalog_cache("zai"))
        (m.catalog_cache_dir() / "zai.json").write_text('{"rows": "not-a-list"}')
        self.assertIsNone(m.read_catalog_cache("zai"))
        (m.catalog_cache_dir() / "zai.json").write_text('{"rows": [[1, 2], [], ["ok"]]}')
        self.assertEqual([r.model_id for r in m.read_catalog_cache("zai")], ["ok"])

    def test_unwritable_cache_never_raises(self):
        m.write_catalog_cache("zai", cat("glm-5.3-flash"))
        # A file where the provider's cache dir must go: mkdir fails, write must not raise.
        (Path(self.tmp) / "blocked").write_text("x")
        os.environ[m.CATALOG_CACHE_DIR_ENV] = str(Path(self.tmp) / "blocked" / "sub")
        m.write_catalog_cache("zai", cat("glm-5.3-flash"))    # must not raise
        self.assertIsNone(m.read_catalog_cache("zai"))


def zai_only_agents():
    return [m.RemoteAgent("mistral", "mistral", "Mistral (choose model)", "unknown", model_id="SELECT"),
            m.RemoteAgent("mistral-large", "mistral", "Mistral Large", "unknown",
                          model_id="mistral-large-latest"),
            m.RemoteAgent("zai", "zai", "ZAI general API (choose model)", "unknown", model_id="SELECT")]


class CatalogCacheScreenTests(unittest.TestCase):
    """The picker screen: instant render from cache, one background revision per session."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fa-cat-screen-test-")
        os.environ[m.CATALOG_CACHE_DIR_ENV] = self.tmp

    def tearDown(self):
        os.environ.pop(m.CATALOG_CACHE_DIR_ENV, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def screen(self, **settings):
        s = m.Settings()
        for k, v in settings.items():
            setattr(s, k, v)
        lane = m.RemoteScreen(s, zai_only_agents(), owner=m.DIRECT_ROOT)
        lane._choose_model_visible = True
        return lane

    def ids(self, lane, group):
        return [i.id for g in lane.groups() if g.id == group for i in g.items]

    def test_cached_rows_render_instantly_and_are_marked_stale(self):
        m.write_catalog_cache("zai", cat("glm-5.3-flash", "glm-5"))
        lane = self.screen()
        lane.load_cached_catalogs()
        self.assertEqual(self.ids(lane, "zai"),
                         ["cat:zai:glm-5.3-flash", "cat:zai:glm-5"])   # no fetch first
        # and the cache-loaded provider is scheduled for exactly one background revision
        self.assertEqual(lane.catalog_kind_needed(), "rcat:mistral")   # open group first
        self.assertEqual(lane.catalog_kind_needed(), "rcat:zai")       # then the revision
        self.assertIsNone(lane.catalog_kind_needed())

    def test_no_cache_still_fresh_fetches(self):
        lane = self.screen()
        lane.load_cached_catalogs()
        self.assertEqual(lane.catalog_kind_needed(), "rcat:mistral")   # open group first
        self.assertEqual(lane.catalog_kind_needed(), "rcat:zai")       # then the drain
        self.assertEqual(self.ids(lane, "zai"), ["cat:zai:__loading__"])

    def test_successful_refresh_replaces_rows_and_writes_cache(self):
        m.write_catalog_cache("zai", cat("glm-5-old"))
        lane = self.screen()
        lane.load_cached_catalogs()
        lane.catalog_kind_needed()                       # open group (mistral)
        lane.catalog_kind_needed()                       # zai revision request
        lane.set_catalog("zai", cat("glm-5.3-flash", "glm-5"), None)
        self.assertEqual(self.ids(lane, "zai"), ["cat:zai:glm-5.3-flash", "cat:zai:glm-5"])
        self.assertEqual([r.model_id for r in m.read_catalog_cache("zai")],
                         ["glm-5.3-flash", "glm-5"])     # cache rewritten from the live list
        self.assertNotIn("zai", lane._catalog_stale)     # revised: no further refresh

    def test_failed_refresh_keeps_cached_rows_and_says_so(self):
        m.write_catalog_cache("zai", cat("glm-5.3-flash", "glm-5"))
        lane = self.screen()
        lane.load_cached_catalogs()
        lane.catalog_kind_needed()
        lane.catalog_kind_needed()
        lane.set_catalog("zai", [], "rcat:zai model list failed to load: HTTP 401")
        self.assertEqual(self.ids(lane, "zai"),
                         ["cat:zai:glm-5.3-flash", "cat:zai:glm-5"])   # never emptied
        header = next(g.label for g in lane.groups() if g.id == "zai")
        self.assertIn("refresh failed", header)
        self.assertIn("HTTP 401", header)
        self.assertEqual([r.model_id for r in m.read_catalog_cache("zai")],
                         ["glm-5.3-flash", "glm-5"])   # the cache file is untouched too

    def test_first_fetch_failure_still_shows_the_error_row(self):
        lane = self.screen()
        lane.load_cached_catalogs()
        lane.catalog_kind_needed()
        lane.set_catalog("mistral", [], "rcat:mistral model list failed to load: HTTP 401")
        self.assertIn("cat:mistral:__error__", self.ids(lane, "mistral"))

    def test_cached_refresh_error_clears_on_later_success(self):
        m.write_catalog_cache("zai", cat("glm-5.3-flash"))
        lane = self.screen()
        lane.load_cached_catalogs()
        lane.catalog_kind_needed()
        lane.catalog_kind_needed()
        lane.set_catalog("zai", [], "rcat:zai model list failed to load: network down")
        lane.set_catalog("zai", cat("glm-5.3-flash"), None)   # a retry succeeded
        header = next(g.label for g in lane.groups() if g.id == "zai")
        self.assertNotIn("refresh failed", header)


if __name__ == "__main__":
    unittest.main()
