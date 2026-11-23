"""Roster rows warm the HF cache too (fix/runtime-model-facts, F4, 2026-11-23).

Measured on the user's machine: 880 HF cache entries, exactly ONE with a context window — the
warm-up ran only when a provider's live CATALOG was opened, never for the roster aliases people
launch. These tests drive the real picker (stub csl inventory, spy on _warm_hf_cache) with NO
catalog loaded at all, so only the roster warm-up can fire. Deliberately NOT load_sensitive:
the picker gate (.rtc/pre-flight.sh PICKER_TESTS) must run them.
"""

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
from picker_harness import PilotCase  # noqa: E402

try:
    import textual  # noqa: F401
except ImportError:
    textual = None
sp = None
if textual is not None:
    import session_picker as sp  # noqa: E402

# alias, provider, display, tier, local_capable, has_key, broken, model_id (8 columns)
ROSTER_INV = ("nvidia-x\tnvidia\tNVIDIA X\tunknown\t0\t1\t0\tacme/model-x\n"
              "gemini-y\tgemini\tGemini Y\trenewing_free\t0\t1\t0\tgoogle/gem-y\n"
              "plain-z\tnvidia\tPlain Z\tunknown\t0\t1\t0\tno-slash-id\n")


@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class RosterWarmGroupsTests(unittest.TestCase):
    def test_groups_hf_shaped_ids_by_provider_once_in_order(self):
        A = type("A", (), {})
        rows = []
        for prov, mid in (("nvidia", "a/x"), ("nvidia", "a/x"), ("nvidia", "b/y"),
                          ("gemini", "g/z"), ("nvidia", "noslash"), ("", "c/w")):
            a = A(); a.provider = prov; a.model_id = mid; rows.append(a)
        self.assertEqual(sp._roster_warm_groups(rows), {"nvidia": ["a/x", "b/y"], "gemini": ["g/z"]})


@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class RosterWarmPilotTests(PilotCase):
    def setUp(self):
        super().setUp()
        self._saved = {k: os.environ.get(k) for k in ("LA_PICKER_HF_LOOKUP", "LA_EAGER_CATALOGS")}
        self.addCleanup(self._restore)
        os.environ["LA_EAGER_CATALOGS"] = "0"            # no live catalog: only the roster can warm
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text("#!/bin/sh\nfor a in \"$@\"; do [ \"$a\" = --inventory ] && { cat <<'EOF'\n"
                        + ROSTER_INV + "EOF\nexit 0; }; done\n")
        stub.chmod(0o755)
        self.warm_calls = []
        orig = sp._warm_hf_cache

        def spy(provider, ids, stop=None):
            del stop
            self.warm_calls.append((provider, list(ids)))
            # the warm-up's EFFECT, written through the REAL locked re-reading store: one warm
            # thread per provider runs concurrently, and an unlocked read-modify-write here lost
            # nvidia's entry to gemini's write (3/3 runs) — the production writer exists for that.
            fp = sp._footprint()
            for mid in ids:
                fp._hf_cache_store(mid.lower(), {"v": 2, "total": 7, "repo": mid, "ctx": 1048576,
                                                 "ctx_done": True, "at": time.time()})
            return []
        sp._warm_hf_cache = spy
        self.addCleanup(setattr, sp, "_warm_hf_cache", orig)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_roster_rows_warm_without_any_catalog_and_gain_context(self):
        os.environ["LA_PICKER_HF_LOOKUP"] = "1"
        seen = {}

        async def scenario(app, pilot, h):
            sm = app.screen_model
            await h.wait_for(lambda: bool(getattr(sm, "agents", None)), "roster loaded", secs=20)
            await h.wait_for(lambda: {p for p, _ in self.warm_calls} >= {"nvidia", "gemini"},
                             "roster warm-up fired for both providers", secs=20)
            # one warm thread per provider: wait for EACH HF-shaped row by name (the first run
            # released on gemini alone and snapshotted before nvidia's refresh landed)
            def both_warm():
                ctx = {a.alias: getattr(a, "context", None) for a in (app.cache.get("remote") or [])}
                return ctx.get("nvidia-x") == 1048576 and ctx.get("gemini-y") == 1048576
            await h.wait_for(both_warm, "both roster rows gained the warmed context", secs=20)
            seen["ctx"] = {a.alias: getattr(a, "context", None) for a in app.cache.get("remote") or []}

        self.run_picker(scenario, start="remote")
        warmed = {mid for _, ids in self.warm_calls for mid in ids}
        self.assertIn("acme/model-x", warmed)
        self.assertIn("google/gem-y", warmed)
        self.assertNotIn("no-slash-id", warmed, "non-HF-shaped ids are not warmed")
        self.assertEqual(seen["ctx"].get("nvidia-x"), 1048576,
                         f"the roster row must carry the warmed context after the refresh: {seen}")

    def test_kill_switch_stops_the_roster_warm_up(self):
        os.environ["LA_PICKER_HF_LOOKUP"] = "0"

        async def scenario(app, pilot, h):
            sm = app.screen_model
            await h.wait_for(lambda: bool(getattr(sm, "agents", None)), "roster loaded", secs=20)
            await pilot.pause(1.0)

        self.run_picker(scenario, start="remote")
        self.assertEqual(self.warm_calls, [], "LA_PICKER_HF_LOOKUP=0 must stop every warm-up")


if __name__ == "__main__":
    unittest.main()
