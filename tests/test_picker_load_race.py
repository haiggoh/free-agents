#!/usr/bin/env python3
"""The remote picker under a slow roster — the 2026-10-25/27 load-race family.

The reported repro (plan "csl crash bugfix", update on 0.46.6): reach "choose model"
while the roster is still loading and type into the live search — the list then stayed
empty behind a false "No API key saved yet" until csl was restarted. Root cause: a
`_regroup()` on the not-yet-landed roster set `_models_loaded = True` and rendered the
keyless instruction, so both fill sites then refused to fill. These tests pin the fix
and the no-keys semantics the fix must preserve.

Usage: python3 tests/test_picker_load_race.py -v   (or pytest)
Environment: none required; stub launchers + temp dirs per picker_harness/conftest.
"""
from pathlib import Path
import os
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_picker_model as m  # noqa: E402
from picker_harness import RECORDER, REMOTE_INV, PilotCase, inventory_stub  # noqa: E402


def slow_inventory_stub(text, secs=3):
    """inventory_stub, but the roster takes `secs` seconds to land: a deterministic
    window in which the UI is live and the roster is not."""
    return ("#!/bin/sh\n"
            f"sleep {secs}\n"
            "for a in \"$@\"; do [ \"$a\" = --inventory ] && { cat <<'EOF'\n" + text +
            "EOF\nexit 0; }; done\n" + RECORDER.split("\n", 1)[1])


class LoadRaceRegressions(PilotCase):
    """The reported repro, end to end against the real picker."""

    def test_search_during_roster_load_does_not_poison_the_screen(self):
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(slow_inventory_stub(REMOTE_INV))
        stub.chmod(0o755)

        async def scenario(app, pilot, h):
            sm = app.screen_model
            # The race window must be OPEN, else this test proves nothing.
            self.assertIn("remote", getattr(app, "_loading", set()),
                          "roster already landed; the stub's sleep is too short")
            self.assertEqual(sm.agents, [])
            sm._toggle_choose_model()      # the user reaches "choose model" at once
            app.render_model()
            sm.set_search("g")             # ...and types before the roster arrives
            app.render_model()
            await h.settle(secs=8)         # the roster lands NOW
            self.assertTrue(sm.agents, "roster never reached the screen — poisoned")
            self.assertNotIn("__nokeys__", [g.id for g in sm.accordion.groups],
                             "an unlanded roster must not read as 'no API key'")
            self.assertTrue(any(not g.id.startswith("__") for g in sm.accordion.groups),
                            "provider groups never rendered after the load race")

        self.run_picker(scenario, start="remote")


class WarmCacheHydration(PilotCase):
    """A remote screen built from a WARM roster cache must paint the providers' disk-cached
    catalogs at once — not only when the roster lands mid-view (the common path never did)."""

    def test_cached_catalogs_paint_when_navigating_to_remote(self):
        m.write_catalog_cache("nvidia", [m.CatalogModel("nvidia/test-cat-row", True)])

        async def scenario(app, pilot, h):
            await h.press("r")             # goto remote with the roster already cached
            sm = app.screen_model
            self.assertIsInstance(sm, m.RemoteScreen)
            self.assertTrue(sm.agents, "precondition: the goto path got the cached roster")
            sm._toggle_choose_model()
            app.render_model()
            self.assertTrue(sm.catalogs.get("nvidia"),
                            "disk-cached catalog never painted on the goto path")
            # By IDENTITY, not display text: catalog rows render humanized names.
            self.assertIn(("item", "cat:nvidia:nvidia/test-cat-row"), app.row_ids,
                          "cached catalog row not rendered among the picker's rows")

        self.run_picker(scenario, start="home")


def models_stub(rows_by_alias):
    """inventory_stub plus `--models <alias>` answers (one TSV row set per provider).
    Catalog calls fall through to the recorder, so launches() sees every fetch."""
    cases = []
    for alias, rows in rows_by_alias.items():
        if rows:
            args = " ".join("'" + r.replace("'", "'\\''") + "'" for r in rows)
            cases.append(f"    {alias}) printf '%s\\n' {args} ;;")
    return ("#!/bin/sh\n"
            "for a in \"$@\"; do [ \"$a\" = --inventory ] && { cat <<'EOF'\n" + REMOTE_INV +
            "EOF\nexit 0; }; done\n"
            "if [ \"$2\" = \"--models\" ]; then\n  case \"$3\" in\n" +
            "\n".join(cases) + "\n  esac\nfi\n" + RECORDER.split("\n", 1)[1])


class EagerStartupFetch(PilotCase):
    """The startup warm-up fetches every keyed provider's catalog once per session, so the
    home count and the model lists are real from the first paint (user decision 2026-10-27)."""

    MODELS = {
        "nvidia-a": ['nvidia/cat-one\t{"tools": true}', "nvidia/cat-two\t{}", "nvidia/cat-three\t{}"],
        "gemini-flash": ["gemini/gcat-one\t{}"],
        "cerebras-oss": [],
    }

    def setUp(self):
        super().setUp()
        os.environ["LA_EAGER_CATALOGS"] = "1"   # the harness default opts the suite out

    def _install_stub(self):
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(models_stub(self.MODELS))
        stub.chmod(0o755)

    def test_every_keyed_provider_catalog_fetches_once_at_startup(self):
        self._install_stub()

        async def scenario(app, pilot, h):
            await h.settle()
            seen = [r for r in self.launches() if "--models" in r["argv"]]
            aliases = {r["argv"][r["argv"].index("--models") + 1] for r in seen}
            self.assertEqual(aliases, {"nvidia-a", "gemini-flash", "cerebras-oss"},
                             f"catalog fetches scheduled for: {aliases or 'nothing'}")

        self.run_picker(scenario, start="home")

    def test_store_seeds_from_disk_and_records_landings(self):
        self._install_stub()
        m.write_catalog_cache("nvidia", [m.CatalogModel("nvidia/c1", True),
                                         m.CatalogModel("nvidia/c2", True)])
        (Path(self.env["LA_REMOTE_CATALOG_CACHE_DIR"]) / "gemini.json").write_text("{corrupt")

        async def scenario(app, pilot, h):
            await h.settle()
            # nvidia: 2 seeded from disk, overwritten by the 3-row landing;
            # gemini: corrupt file seeds nothing, its landing records 1; cerebras: 0 rows.
            self.assertEqual(app.catalog_counts, {"nvidia": 3, "gemini": 1, "cerebras": 0})
            self.assertEqual([c.model_id for c in app.catalog_rows["nvidia"]],
                             ["nvidia/cat-one", "nvidia/cat-two", "nvidia/cat-three"])
            self.assertEqual(app.catalog_rows["cerebras"], [])

        self.run_picker(scenario, start="home")


class NoKeysSemantics(unittest.TestCase):
    """`__nokeys__` is an instruction for a keyless roster — never for an unlanded one."""

    def test_keyless_roster_still_shows_the_nokeys_instruction(self):
        agents = [m.RemoteAgent(f"a{i}", "prov", f"Model {i}", "free", has_key=False)
                  for i in range(3)]
        lane = m.RemoteScreen(m.Settings(), agents, owner=m.DIRECT_ROOT)
        self.assertIn("__nokeys__", [g.id for g in lane.accordion.groups])
        self.assertTrue(lane._models_loaded)

    def test_unlanded_roster_renders_neither_nokeys_nor_loaded(self):
        lane = m.RemoteScreen(m.Settings(), [], owner=m.DIRECT_ROOT)
        self.assertNotIn("__nokeys__", [g.id for g in lane.accordion.groups])
        self.assertFalse(lane._models_loaded)


if __name__ == "__main__":
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__.strip())
        sys.exit(0)
    unittest.main()
