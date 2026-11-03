#!/usr/bin/env python3
"""tests/test_picker_async_load_0507.py — 0.50.8: providers must not wait for each other.

User (verbatim): providers "shouldn't wait for each other to draw their menu entries…
what's already loaded should also show as soon as its ready… at least on a per-group
basis". Concretely pinned here:

  a. the LOCAL list renders while the remote roster is still loading (never blocked on it);
  b. group headers render as soon as the roster lands, catalogs still pending;
  c. each pending provider group shows its OWN animated loading placeholder (stable row id,
     moving label — a changing id would be the DuplicateID crash class of 2026-11-02);
  d. each group's rows fill as soon as THAT provider's catalog GET lands — the serial HF
     warm-up must run in the background AFTER the rows deliver, not before them.

Mutation anchors: (d) re-adding the synchronous `_warm_hf_cache` inside `load_remote_catalog`
turns test_rows_land_before_hf_warmup_completes and test_load_remote_catalog_returns_rows_
without_warming red; making `render_model` wait for ALL providers turns the per-group test
red; a static placeholder label turns the animation test red.

Usage: python3 tests/test_picker_async_load_0507.py -v   (or pytest)
"""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import session_picker_model as m  # noqa: E402
from picker_harness import RECORDER, REMOTE_INV, PilotCase  # noqa: E402

# session_picker sys.exit(3)s at import when textual is missing, which would INTERNALERROR the
# plain `pytest` runner — so only import it when textual is present (repo convention: pilot
# tests skip under the pipx runner, run under the picker venv python).
try:
    import textual  # noqa: F401
except ImportError:
    textual = None
sp = None
if textual is not None:
    import session_picker as sp  # noqa: E402

pytestmark = pytest.mark.load_sensitive  # drives the real picker in pilot processes

CAT_NVIDIA = 'org/nvidia-alpha\t{"tools": true}\norg/nvidia-beta\t{"tools": true}\n'
CAT_GEMINI = 'org/gemini-alpha\t{"tools": true}\n'


def models_stub(slow_alias=None, secs=3.0):
    """remote-session.sh stub: `--inventory` (the roster) lands at once; `--models <alias>`
    answers with that provider's catalog — sleeping first when alias == slow_alias, so that
    provider's rows land late while the others arrive immediately."""
    return (
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = --inventory ]; then cat <<\'EOF\'\n' + REMOTE_INV + "EOF\nexit 0; fi\n"
        '  if [ "$a" = --models ]; then\n'
        '    alias=""; prev=""\n'
        '    for a in "$@"; do [ "$prev" = --models ] && alias="$a"; prev="$a"; done\n'
        f'    if [ "$alias" = "{slow_alias}" ]; then sleep {secs}; fi\n'
        '    case "$alias" in\n'
        f'      nvidia-a) printf \'%s\' \'{CAT_NVIDIA}\' ;;\n'
        f'      gemini-flash) printf \'%s\' \'{CAT_GEMINI}\' ;;\n'
        "      *) : ;;\n"
        "    esac\n"
        "    exit 0\n"
        "  fi\n"
        "done\n" + RECORDER.split("\n", 1)[1]
    )


def slow_inventory_stub(text, secs=3.0):
    """The roster itself lands late: a deterministic window with the UI live but no roster."""
    return ("#!/bin/sh\n"
            f"sleep {secs}\n"
            'for a in "$@"; do [ "$a" = --inventory ] && { cat <<\'EOF\'\n' + text +
            "EOF\nexit 0; }; done\n" + RECORDER.split("\n", 1)[1])


@pytest.fixture(autouse=True)
def _sandbox_catalog_cache():
    """Same sandbox as test_picker_load_race: nothing here may touch the real catalog cache."""
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


@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class CatalogWarmupOffCriticalPath(unittest.TestCase):
    """(d) at the loader level: load_remote_catalog must return rows without warming HF first."""

    def setUp(self):
        self._saved = os.environ.get("LA_PICKER_HF_LOOKUP")
        os.environ["LA_PICKER_HF_LOOKUP"] = "1"      # warm-up enabled: a sync call would run
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("LA_PICKER_HF_LOOKUP", None)
        else:
            os.environ["LA_PICKER_HF_LOOKUP"] = self._saved

    def test_load_remote_catalog_returns_rows_without_warming(self):
        assert sp is not None            # skipUnless guarantees this at runtime

        def fake_run(cmd):
            return mock.Mock(returncode=0, stdout=CAT_NVIDIA, stderr="")

        with mock.patch.object(sp, "_run", fake_run), \
                mock.patch.object(sp, "_warm_hf_cache") as warm:
            rows = sp.load_remote_catalog("rcat:nvidia-a")
        self.assertEqual([r.model_id for r in rows], ["org/nvidia-alpha", "org/nvidia-beta"])
        warm.assert_not_called()   # red if the warm-up moves back into the loader


class PlaceholderAnimation(unittest.TestCase):
    """(c) the pending group's placeholder animates but its row ID is stable (DuplicateID guard)."""

    def _screen(self):
        agents = [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown", has_key=True),
                  m.RemoteAgent("gemini-flash", "gemini", "Gemini Flash", "renewing_free", has_key=True)]
        sm = m.RemoteScreen(m.Settings(), agents)
        sm._regroup()
        return sm

    def test_placeholder_label_animates_id_stays(self):
        sm = self._screen()
        sm._catalog_pending.add("gemini")
        pinned = [a for a in sm.agents if a.provider == "gemini"]
        sm.loading_frame = 0
        item0 = sm._catalog_items("gemini", pinned)
        sm.loading_frame = 4
        item4 = sm._catalog_items("gemini", pinned)
        self.assertEqual(len(item0), 1)
        self.assertIn("__loading__", item0[0].id)
        self.assertEqual(item0[0].id, item4[0].id,
                         "the loading row's id must not change between ticks (DuplicateID class)")
        self.assertNotEqual(item0[0].label, item4[0].label,
                            "the loading placeholder must animate with the tick")


class PerGroupFill(PilotCase):
    """(b)+(d) end to end: headers at once, group A fills while B is still pending, B lands
    after, no duplicate row ids on any re-render."""

    def setUp(self):
        super().setUp()
        self._saved_eager = os.environ.get("LA_EAGER_CATALOGS")
        os.environ["LA_EAGER_CATALOGS"] = "1"     # harness defaults it to 0; this test needs the fetches
        self.addCleanup(self._restore_eager)

    def _restore_eager(self):
        if self._saved_eager is None:
            os.environ.pop("LA_EAGER_CATALOGS", None)
        else:
            os.environ["LA_EAGER_CATALOGS"] = self._saved_eager

    def _install_stub(self, **kw):
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(models_stub(**kw))
        stub.chmod(0o755)

    def _group_items(self, sm, prov):
        g = next(g for g in sm.accordion.groups if g.id == prov)
        return g.items

    def test_group_a_fills_while_b_pending_then_b_lands(self):
        self._install_stub(slow_alias="gemini-flash", secs=3.0)

        async def scenario(app, pilot, h):
            sm = app.screen_model
            self.assertIsInstance(sm, m.RemoteScreen)
            sm._toggle_choose_model()
            app.render_model()
            # (b) headers render as soon as the roster lands — before any catalog
            await h.wait_for(lambda: bool(sm.agents), "remote roster", secs=10)
            app.render_model()
            self.assertIn("nvidia", [g.id for g in sm.accordion.groups])
            self.assertIn("gemini", [g.id for g in sm.accordion.groups])
            # (d) nvidia's catalog lands while gemini's stub is still sleeping
            await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list),
                             "nvidia catalog", secs=10)
            app.render_model()
            nvidia_ids = [i.id for i in self._group_items(sm, "nvidia")]
            self.assertIn("cat:nvidia:org/nvidia-alpha", nvidia_ids)
            gemini_items = self._group_items(sm, "gemini")
            self.assertTrue(any("__loading__" in i.id for i in gemini_items),
                            f"gemini group must show its own loading placeholder, got {gemini_items}")
            self.assertNotIn("cat:gemini:org/gemini-alpha",
                             [i.id for i in gemini_items])
            # ...and B lands later, without waiting for anything else
            await h.wait_for(lambda: isinstance(sm.catalogs.get("gemini"), list),
                             "gemini catalog", secs=15)
            app.render_model()
            gemini_ids = [i.id for i in self._group_items(sm, "gemini")]
            self.assertIn("cat:gemini:org/gemini-alpha", gemini_ids)
            self.assertFalse(any("__loading__" in i for i in gemini_ids))
            # no DuplicateID on any re-render through the partial states above
            ids = [r for r in app.row_ids if r]
            self.assertEqual(len(ids), len(set(ids)), f"duplicate row ids: {ids}")

        self.run_picker(scenario, start="remote")

    def test_rows_land_before_hf_warmup_completes(self):
        """(d) the serial HF warm-up runs in the BACKGROUND after the rows are delivered."""
        self._install_stub()
        saved_lookup = os.environ.get("LA_PICKER_HF_LOOKUP")
        os.environ["LA_PICKER_HF_LOOKUP"] = "1"
        warm_done = threading.Event()
        orig_warm = self.sp._warm_hf_cache

        def fake_warm(ids):
            time.sleep(0.5)          # stand-in for the real (slow) serial warm-up
            warm_done.set()

        self.sp._warm_hf_cache = fake_warm

        async def scenario(app, pilot, h):
            try:
                sm = app.screen_model
                sm._toggle_choose_model()
                app.render_model()
                await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list),
                                 "nvidia rows delivered", secs=10)
                self.assertFalse(warm_done.is_set(),
                                 "rows landed only after the warm-up finished — the 0.50.8 regression")
                await h.wait_for(warm_done.is_set, "background warm-up", secs=10)
            finally:
                self.sp._warm_hf_cache = orig_warm
                if saved_lookup is None:
                    os.environ.pop("LA_PICKER_HF_LOOKUP", None)
                else:
                    os.environ["LA_PICKER_HF_LOOKUP"] = saved_lookup

        self.run_picker(scenario, start="remote")


class LocalNotBlockedOnRemote(PilotCase):
    """(a) the local model list renders while the remote roster is still in flight."""

    def test_local_rows_paint_during_remote_roster_load(self):
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(slow_inventory_stub(REMOTE_INV, secs=3.0))
        stub.chmod(0o755)

        async def scenario(app, pilot, h):
            sm = app.screen_model
            self.assertIsInstance(sm, m.LocalScreen)
            sm._toggle_choose_model()
            app.render_model()
            await h.wait_for(lambda: getattr(sm, "_models_loaded", False), "local inventory", secs=10)
            app.render_model()
            self.assertIn("remote", getattr(app, "_loading", set()),
                          "the remote roster must still be loading in this window")
            local_items = [r for r in app.row_ids if r and r[0] == "item"]
            self.assertTrue(local_items, "local rows must paint while remote is still loading")

        self.run_picker(scenario, start="local")


if __name__ == "__main__":
    unittest.main()
