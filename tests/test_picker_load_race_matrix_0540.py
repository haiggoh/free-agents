#!/usr/bin/env python3
"""0.54.0 Task 5 — the load/paint race matrix.

The changelog "fixed" the remote roster load/paint race in 13 releases (0.22.3…0.51.0) because
rows were pushed in from >=5 sites with race-dependent guards. This file pins the OUTCOME:
whatever the order of (screen build, roster load, catalog loads, keystrokes, disk-cache-only),
the final row-id list on the Remote AND the Lowkey screen equals the reference run where all
data is present before the screen is built.

Ordering is gated with files the test releases (never with sleep-driven assertions).
Run under the picker venv python: pytest tests/test_picker_load_race_matrix_0540.py
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
from picker_harness import RECORDER, REMOTE_INV, PilotCase  # noqa: E402

try:
    import textual  # noqa: F401
except ImportError:
    textual = None
sp = None
if textual is not None:
    import session_picker as sp  # noqa: E402

pytestmark = pytest.mark.load_sensitive

CAT_NVIDIA = 'org/nvidia-alpha\t{"tools": true}\norg/nvidia-beta\t{"tools": true}\n'
CAT_GEMINI = 'org/gemini-alpha\t{"tools": true}\n'


def fast_models_stub():
    """inventory + both catalogs answer immediately."""
    return (
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = --inventory ]; then cat <<\'EOF\'\n' + REMOTE_INV + "EOF\nexit 0; fi\n"
        '  if [ "$a" = --models ]; then\n'
        '    alias=""; prev=""\n'
        '    for a in "$@"; do [ "$prev" = --models ] && alias="$a"; prev="$a"; done\n'
        '    case "$alias" in\n'
        '      nvidia-a) printf \'%s\' \'' + CAT_NVIDIA + "' ;;\n"
        '      gemini-flash) printf \'%s\' \'' + CAT_GEMINI + "' ;;\n"
        "      *) : ;;\n"
        "    esac\n"
        "    exit 0\n"
        "  fi\n"
        "done\n" + RECORDER.split("\n", 1)[1]
    )


def gated_inventory_stub(text, gate):
    """The ROSTER waits for the gate file (released by the test); catalogs answer at once."""
    return (
        "#!/bin/sh\n"
        f'for a in "$@"; do\n'
        f'  if [ "$a" = --inventory ]; then\n'
        f'    while [ ! -f "{gate}" ]; do sleep 0.05; done\n'
        f"    cat <<'EOF'\n" + text + "EOF\nexit 0; fi\n"
        f'  if [ "$a" = --models ]; then\n'
        f'    alias=""; prev=""\n'
        f'    for a in "$@"; do [ "$prev" = --models ] && alias="$a"; prev="$a"; done\n'
        f'    case "$alias" in\n'
        f'      nvidia-a) printf \'%s\' \'{CAT_NVIDIA}\' ;;\n'
        f'      gemini-flash) printf \'%s\' \'{CAT_GEMINI}\' ;;\n'
        f'      *) : ;;\n'
        f'    esac\n'
        f'    exit 0\n'
        f'  fi\n'
        f'done\n' + RECORDER.split("\n", 1)[1]
    )


def gated_catalog_stub(gate, slow_alias):
    """Roster at once; the slow_alias's CATALOG waits for the gate file."""
    return (
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = --inventory ]; then cat <<\'EOF\'\n' + REMOTE_INV + "EOF\nexit 0; fi\n"
        '  if [ "$a" = --models ]; then\n'
        '    alias=""; prev=""\n'
        '    for a in "$@"; do [ "$prev" = --models ] && alias="$a"; prev="$a"; done\n'
        f'    if [ "$alias" = "{slow_alias}" ]; then while [ ! -f "{gate}" ]; do sleep 0.05; done; fi\n'
        '    case "$alias" in\n'
        '      nvidia-a) printf \'%s\' \'' + CAT_NVIDIA + "' ;;\n"
        '      gemini-flash) printf \'%s\' \'' + CAT_GEMINI + "' ;;\n"
        "      *) : ;;\n"
        "    esac\n"
        "    exit 0\n"
        "  fi\n"
        "done\n" + RECORDER.split("\n", 1)[1]
    )


def empty_catalog_stub():
    """Roster at once; --models answers NOTHING (an empty live reply is inconclusive -> disk cache)."""
    return (
        "#!/bin/sh\n"
        'for a in "$@"; do [ "$a" = --inventory ] && { cat <<\'EOF\'\n' + REMOTE_INV +
        "EOF\nexit 0; }; done\n" + RECORDER.split("\n", 1)[1]
    )



def _settled(app, scan_placeholders=True):
    """No model-row loads in flight and no pending catalog fetches; with scan_placeholders,
    also no transient __loading__/__error__ placeholder rows (UI state, not a row difference).
    versions: loads don't paint model rows and can legitimately outlive the matrix."""
    sm = app.screen_model
    if any(not k.startswith("versions:") for k in getattr(app, "_loading", set())):
        return False
    if getattr(sm, "_catalog_pending", set()) or getattr(sm, "_fetch_in_flight", set()):
        return False
    if scan_placeholders:
        return not any("__loading__" in r or "__error__" in r for r in row_ids(app))
    return True

def row_ids(app):
    """The final row-id list from the screen MODEL: every group and item id (open or folded,
    nested family groups recursed), sorted. accordion.rows() only yields OPEN groups, so walk
    the group tree directly."""
    ids = []

    def walk(gs):
        for g in gs:
            ids.append(f"group:{g.id}")
            for it in g.items:
                if isinstance(it, m.Group):
                    walk([it])                    # nested family group used as an item
                else:
                    ids.append(f"item:{it.id}")
            walk(getattr(g, "groups", None) or [])

    walk(app.screen_model.accordion.groups)
    return sorted(ids)


@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class LoadRaceMatrix(PilotCase):
    """Each case asserts equality with the reference run on BOTH the Remote and the Lowkey screen."""

    def setUp(self):
        super().setUp()
        self._saved_eager = os.environ.get("LA_EAGER_CATALOGS")
        os.environ["LA_EAGER_CATALOGS"] = "1"     # catalogs are part of the matrix
        self.addCleanup(self._restore_eager)
        self._refs = {}

    def _restore_eager(self):
        if self._saved_eager is None:
            os.environ.pop("LA_EAGER_CATALOGS", None)
        else:
            os.environ["LA_EAGER_CATALOGS"] = self._saved_eager

    def _install(self, text):
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(text)
        stub.chmod(0o755)

    def _reference_rows(self, start):
        """All data present before the screen is built: fast stubs, full wait, collect rows."""
        if start in self._refs:
            return self._refs[start]
        self._install(fast_models_stub())
        holder = {}

        async def scenario(app, pilot, h):
            sm = app.screen_model
            if hasattr(sm, "_toggle_choose_model"):
                sm._toggle_choose_model()
                app.render_model()
            if isinstance(sm, m.RemoteScreen):
                await h.wait_for(lambda: bool(sm.agents) and sm._models_loaded, "roster", secs=15)
                await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list)
                                 and isinstance(sm.catalogs.get("gemini"), list), "catalogs", secs=20)
            else:
                await h.wait_for(lambda: getattr(sm, "_models_loaded", False) and sm.models
                                 and any(g.id == "api" and g.items for g in sm.accordion.groups),
                                 "lowkey api+local", secs=15)
            await h.wait_for(lambda: _settled(app, scan_placeholders=False), "settled", secs=40)
            app.render_model()
            await pilot.pause()
            holder["rows"] = row_ids(app)

        self.run_picker(scenario, start=start)
        rows = holder["rows"]
        assert rows, "reference row list must be non-empty"
        self._refs[start] = rows
        return rows

    @staticmethod
    def _roster_pending(sm):
        """True while the remote roster has not landed on this screen yet."""
        if isinstance(sm, m.RemoteScreen):
            return not sm.agents or not sm._models_loaded
        return not getattr(sm, "api_agents", None)      # LowkeyScreen fills api_agents on land

    def _check_reference_shape(self, start, rows):
        if start == "remote":
            cats = [r for r in rows if r.startswith("item:cat:")]
            provs = {r.split(":")[2] for r in cats if r.count(":") >= 3}
            self.assertGreaterEqual(len(provs & {"nvidia", "gemini"}), 2,
                                    f"reference must carry catalog rows for >=2 providers: {provs}")
        else:
            self.assertTrue(any(r.startswith("item:nvidia:") for r in rows), "lowkey ref: api rows")
            self.assertTrue(any(r.startswith("item:gemini:") for r in rows), "lowkey ref: 2nd provider")
            self.assertTrue(any("qwen-3.8-operator" in r for r in rows), "lowkey ref: local rows")

    def _assert_matches_reference(self, start, app, rows):
        assert start in self._refs, "reference must be collected before the race run"
        ref = self._refs[start]
        self._check_reference_shape(start, ref)
        self.assertEqual(rows, ref, f"{start}: race produced different rows than the reference")

    # --- the five race cases, each on both screens ------------------------------------------

    def test_screen_before_data(self):
        self._reference_rows("remote")
        self._reference_rows("lowkey")
        for start in ("remote", "lowkey"):
            with self.subTest(screen=start):
                gate = Path(self.tmp.name) / f"gate-{start}"
                self._install(gated_inventory_stub(REMOTE_INV, gate))

                async def scenario(app, pilot, h):
                    sm = app.screen_model
                    if hasattr(sm, "_toggle_choose_model"):
                        sm._toggle_choose_model()
                        app.render_model()
                    await h.wait_for(lambda: self._roster_pending(sm), "screen before data", secs=10)
                    gate.touch()                      # release the roster
                    if isinstance(sm, m.RemoteScreen):
                        await h.wait_for(lambda: bool(sm.agents) and sm._models_loaded, "roster", secs=15)
                        await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list)
                                         and isinstance(sm.catalogs.get("gemini"), list),
                                         "catalogs", secs=20)
                    else:
                        await h.wait_for(lambda: getattr(sm, "_models_loaded", False) and sm.models
                                         and any(g.id == "api" and g.items for g in sm.accordion.groups),
                                         "lowkey filled", secs=15)
                    await h.wait_for(lambda: _settled(app), "settled (no pending loads/placeholders)", secs=40)
                    app.render_model()
                    await pilot.pause()
                    self._assert_matches_reference(start, app, row_ids(app))

                self.run_picker(scenario, start=start)

    def test_data_before_screen(self):
        self._reference_rows("remote")
        self._reference_rows("lowkey")
        for start in ("remote", "lowkey"):
            with self.subTest(screen=start):
                self._install(fast_models_stub())

                async def scenario(app, pilot, h):
                    # data lands at HOME first (startup prefetch), the screen is built after
                    await h.wait_for(lambda: "remote" in app.cache and "local" in app.cache,
                                     "prefetch at home", secs=15)
                    if isinstance(app.screen_model, m.RemoteScreen):
                        await h.wait_for(lambda: isinstance(app.screen_model.catalogs.get("nvidia"), list)
                                         and isinstance(app.screen_model.catalogs.get("gemini"), list),
                                         "catalogs at home", secs=20)
                    app.goto(m.Nav(start, m.DIRECT_ROOT))
                    sm = app.screen_model
                    if hasattr(sm, "_toggle_choose_model"):
                        sm._toggle_choose_model()
                    app.render_model()
                    await h.wait_for(lambda: getattr(sm, "_models_loaded", False), "screen filled", secs=10)
                    await h.wait_for(lambda: _settled(app), "settled (no pending loads/placeholders)", secs=40)
                    app.render_model()
                    await pilot.pause()
                    self._assert_matches_reference(start, app, row_ids(app))

                self.run_picker(scenario, start="home")

    def test_keystroke_during_load(self):
        self._reference_rows("remote")
        self._reference_rows("lowkey")
        for start in ("remote", "lowkey"):
            with self.subTest(screen=start):
                gate = Path(self.tmp.name) / f"gate-k-{start}"
                self._install(gated_inventory_stub(REMOTE_INV, gate))

                async def scenario(app, pilot, h):
                    sm = app.screen_model
                    if hasattr(sm, "_toggle_choose_model"):
                        sm._toggle_choose_model()
                        app.render_model()
                    # a keystroke WHILE the roster is in flight must not corrupt the rows:
                    # on list screens with a live filter that is typing into it; on Lowkey
                    # (no search bar until Task 2) it is navigation keys.
                    await h.press("down")            # focus the rows list
                    if hasattr(sm, "set_search"):
                        await h.press("n")
                    else:
                        await h.press("down")
                    await pilot.pause()
                    gate.touch()
                    if isinstance(sm, m.RemoteScreen):
                        await h.wait_for(lambda: bool(sm.agents) and sm._models_loaded, "roster", secs=15)
                        await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list)
                                         and isinstance(sm.catalogs.get("gemini"), list),
                                         "catalogs", secs=20)
                    else:
                        await h.wait_for(lambda: getattr(sm, "_models_loaded", False) and sm.models
                                         and any(g.id == "api" and g.items for g in sm.accordion.groups),
                                         "lowkey filled", secs=15)
                    # clear the filter typed during the load (list screens with a search bar)
                    if hasattr(sm, "set_search"):
                        sm.set_search("")
                        app.query_one("#search").value = ""
                    await h.wait_for(lambda: _settled(app), "settled (no pending loads/placeholders)", secs=40)
                    app.render_model()
                    await pilot.pause()
                    self._assert_matches_reference(start, app, row_ids(app))

                self.run_picker(scenario, start=start)

    def test_disk_cache_only(self):
        self._reference_rows("remote")
        self._reference_rows("lowkey")
        for start in ("remote", "lowkey"):
            with self.subTest(screen=start):
                # seed the DISK catalog cache; the live --models answers nothing
                from session_picker_model import CatalogModel
                m.write_catalog_cache("nvidia", [CatalogModel("org/nvidia-alpha"),
                                                CatalogModel("org/nvidia-beta")])
                m.write_catalog_cache("gemini", [CatalogModel("org/gemini-alpha")])
                self._install(empty_catalog_stub())

                async def scenario(app, pilot, h):
                    sm = app.screen_model
                    if hasattr(sm, "_toggle_choose_model"):
                        sm._toggle_choose_model()
                        app.render_model()
                    if isinstance(sm, m.RemoteScreen):
                        await h.wait_for(lambda: bool(sm.agents) and sm._models_loaded, "roster", secs=15)
                        await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list)
                                         and isinstance(sm.catalogs.get("gemini"), list),
                                         "disk catalogs", secs=20)
                    else:
                        await h.wait_for(lambda: getattr(sm, "_models_loaded", False) and sm.models
                                         and any(g.id == "api" and g.items for g in sm.accordion.groups),
                                         "lowkey filled", secs=15)
                    await h.wait_for(lambda: _settled(app), "settled (no pending loads/placeholders)", secs=40)
                    app.render_model()
                    await pilot.pause()
                    self._assert_matches_reference(start, app, row_ids(app))

                self.run_picker(scenario, start=start)

    def test_late_catalog_after_open(self):
        self._reference_rows("remote")
        self._reference_rows("lowkey")
        for start in ("remote",):
            with self.subTest(screen=start):
                gate = Path(self.tmp.name) / "gate-late"
                self._install(gated_catalog_stub(gate, "gemini-flash"))

                async def scenario(app, pilot, h):
                    sm = app.screen_model
                    sm._toggle_choose_model()
                    app.render_model()
                    await h.wait_for(lambda: bool(sm.agents) and sm._models_loaded, "roster", secs=15)
                    # open the nvidia group FIRST; gemini's catalog lands after
                    await h.goto_row("group", "nvidia")
                    await h.press("right")
                    await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list), "nvidia catalog", secs=15)
                    app.render_model()
                    gate.touch()                      # gemini catalog lands now
                    await h.wait_for(lambda: isinstance(sm.catalogs.get("gemini"), list),
                                     "late gemini catalog", secs=20)
                    await h.wait_for(lambda: _settled(app), "settled (no pending loads/placeholders)", secs=40)
                    app.render_model()                # NO keystroke — rows must paint
                    await pilot.pause()
                    self._assert_matches_reference(start, app, row_ids(app))

                self.run_picker(scenario, start=start)


if __name__ == "__main__":
    unittest.main()
