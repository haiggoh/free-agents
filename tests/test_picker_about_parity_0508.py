#!/usr/bin/env python3
"""tests/test_picker_about_parity_0508.py — 0.50.8 Task 5: About remote count == remote picker
subheadline.

User: the About screen's remote model numbers must match the remote picker subheadline. Root
cause: two separate counting code paths (About counted agents-only via lane._visible; the
subheadline also counts landed catalog rows). Fixed by extracting ONE counting function
(m.remote_visibility_counts) used by both, over the same data — About hydrates the same
catalogs the remote screen would see (disk cache + session fetches).

Pinned here: (1) the shared function's numbers equal the RemoteScreen subheadline's parsed
numbers for the same stub inventory, including a broken/hidden row and catalog-only rows;
(2) parity holds with a PARTIALLY loaded catalog (one provider landed, one pending) — both
sides count landed rows; (3) end to end in a pilot: About stats == remote subheadline against
the same stubbed roster + catalog.

Mutation anchor: reverting the subheadline to agents-only counting (or About to agents-only)
makes the parity assertions red.

Usage: python3 tests/test_picker_about_parity_0508.py -v   (or pytest)
"""
import os
import re
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import session_picker_model as m  # noqa: E402
from picker_harness import RECORDER, REMOTE_INV, PilotCase  # noqa: E402

try:
    import textual  # noqa: F401
except ImportError:
    textual = None

pytestmark = pytest.mark.load_sensitive


def stub_agents():
    return [
        m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown", has_key=True, model_id="nvidia/m-a"),
        m.RemoteAgent("gemini-flash", "gemini", "Gemini Flash", "renewing_free", has_key=True, model_id="gemini/m-f"),
        m.RemoteAgent("broken-one", "nvidia", "Broken One", "unknown", has_key=True, broken=True, model_id="nvidia/m-broken"),
    ]


def parse_policy(policy: str):
    mo = re.search(r"(\d+) model\(s\) visible\s+\(hidden: (\d+)\)", policy)
    assert mo, f"unparseable subheadline: {policy!r}"
    return int(mo.group(1)), int(mo.group(2))


class SharedCounting(unittest.TestCase):
    def test_counts_match_subheadline_same_inventory(self):
        s = m.Settings()
        agents = stub_agents()
        catalogs = {"nvidia": [m.CatalogModel("nvidia/m-a"),      # already pinned: not double-counted
                               m.CatalogModel("nvidia/m-cat1"),
                               m.CatalogModel("nvidia/m-cat2")],
                    "gemini": [m.CatalogModel("gemini/m-cat1")]}
        vis, hidden = m.remote_visibility_counts(s, agents, catalogs)
        sm = m.RemoteScreen(s, agents)
        sm._regroup()
        sm.catalogs = catalogs
        pv, ph = parse_policy(sm.policy)
        self.assertEqual((vis, hidden), (pv, ph),
                         f"shared count {(vis, hidden)} != subheadline {(pv, ph)}")
        # sanity: 2 visible pinned + 2 nvidia catalog-only + 1 gemini catalog-only = 5;
        # the broken row is hidden
        self.assertEqual((vis, hidden), (5, 1))

    def test_parity_with_partially_loaded_catalog(self):
        s = m.Settings()
        agents = stub_agents()
        catalogs = {"nvidia": [m.CatalogModel("nvidia/m-cat1")]}   # gemini not landed yet
        vis, hidden = m.remote_visibility_counts(s, agents, catalogs)
        sm = m.RemoteScreen(s, agents)
        sm._regroup()
        sm.catalogs = catalogs
        sm._catalog_pending.add("gemini")                          # gemini still fetching
        pv, ph = parse_policy(sm.policy)
        self.assertEqual((vis, hidden), (pv, ph))
        self.assertEqual((vis, hidden), (3, 1))


def models_stub():
    """remote-session.sh stub: --inventory immediate; --models <alias> answers a 2-row catalog."""
    cat = 'org/cat-a\t{"tools": true}\norg/cat-b\t{"tools": true}\n'
    return ("#!/bin/sh\n"
            'for a in "$@"; do\n'
            '  if [ "$a" = --inventory ]; then cat <<\'EOF\'\n' + REMOTE_INV + "EOF\nexit 0; fi\n"
            '  if [ "$a" = --models ]; then printf \'%s\' \'' + cat + '\'; exit 0; fi\n'
            "done\n" + RECORDER.split("\n", 1)[1])


@pytest.fixture(autouse=True)
def _sandbox_catalog_cache():
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="fa-cat-cache-")
    old = os.environ.get(m.CATALOG_CACHE_DIR_ENV)
    os.environ[m.CATALOG_CACHE_DIR_ENV] = tmp
    yield
    if old is None:
        os.environ.pop(m.CATALOG_CACHE_DIR_ENV, None)
    else:
        os.environ[m.CATALOG_CACHE_DIR_ENV] = old
    shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(textual is not None, "picker venv missing: run under the picker venv python")
class AboutParityPilot(PilotCase):
    def setUp(self):
        super().setUp()
        self._saved_eager = os.environ.get("LA_EAGER_CATALOGS")
        os.environ["LA_EAGER_CATALOGS"] = "1"
        self.addCleanup(self._restore_eager)
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(models_stub())
        stub.chmod(0o755)

    def _restore_eager(self):
        if self._saved_eager is None:
            os.environ.pop("LA_EAGER_CATALOGS", None)
        else:
            os.environ["LA_EAGER_CATALOGS"] = self._saved_eager

    def test_about_stats_equal_remote_subheadline(self):
        async def scenario(app, pilot, h):
            # About first: stats gather waits for the roster, then lands
            await h.press("i")                     # About action on home
            await h.wait_for(lambda: isinstance(app.screen_model, m.AboutScreen), "about screen", secs=8)
            await h.wait_for(lambda: isinstance(app.settings.stats, dict)
                             and "remote_visible" in app.settings.stats, "about stats", secs=20)
            # let the eager catalogs land too, then compare against the remote screen
            await h.wait_for(lambda: getattr(app, "catalog_rows", None)
                             and all(p in app.catalog_rows for p in ("nvidia", "gemini", "cerebras")),
                             "catalogs", secs=20)
            app.goto(m.Nav("remote", m.DIRECT_ROOT))   # remote screen (About has no lane keys)
            await h.settle(secs=1.0)
            sm = app.screen_model
            self.assertIsInstance(sm, m.RemoteScreen)
            sm._toggle_choose_model()
            app.render_model()
            await h.wait_for(lambda: isinstance(sm.policy, str) and "model(s) visible" in sm.policy,
                             "subheadline", secs=10)
            pv, ph = parse_policy(sm.policy)
            av, ah = app.settings.stats["remote_visible"], app.settings.stats.get("remote_hidden", 0)
            self.assertEqual((av, ah), (pv, ph),
                             f"About {(av, ah)} != subheadline {(pv, ph)}")

        self.run_picker(scenario, start="home")


if __name__ == "__main__":
    unittest.main()
