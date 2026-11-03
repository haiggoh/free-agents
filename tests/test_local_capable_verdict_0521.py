"""tests/test_local_capable_verdict_0521.py — 0.52.1: the local-capable verdict must use the
REAL app function (not a stub) — the 0.52.0 claim was paper-only because the verdict path
ignored PARAM_COUNT_OVERRIDES and its offline HF rung, so vendor-documented small models
(arctic-embed-l 0.3B, kosmos-2 1.7B, laguna-xs-2.1 33B) were never judged capable.
Runs under the picker venv (imports the app module, which needs rich/textual) — registered
in .rtc/pre-flight.sh PICKER_TESTS. Budget pinned via LA_MEMORY_BUDGET_GB for determinism."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class LocalCapableVerdictReal(unittest.TestCase):
    def _sp(self):
        from picker_harness import ROOT
        sys.path.insert(0, str(ROOT / "bin"))
        import session_picker as sp
        return sp

    def _model(self):
        from picker_harness import ROOT
        sys.path.insert(0, str(ROOT / "bin"))
        import session_picker_model as m
        return m

    def test_overrides_make_small_models_capable_big_ones_not(self):
        sp = self._sp()
        with mock.patch.dict(os.environ, {"LA_MEMORY_BUDGET_GB": "100"}):
            sp._VERDICT_MEMO.clear()
            self.assertTrue(sp.catalog_local_capable("nvidia", "snowflake-arctic-embed-l"))
            self.assertTrue(sp.catalog_local_capable("nvidia", "kosmos-2"))
            self.assertTrue(sp.catalog_local_capable("nvidia", "laguna-xs-2.1"))
            self.assertFalse(sp.catalog_local_capable("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"))
            self.assertFalse(sp.catalog_local_capable("nvidia", "glm-5.3-flash"))

    def test_pinned_rows_get_the_real_verdict_and_filter_by_default(self):
        sp = self._sp()
        m = self._model()
        agents = [
            m.RemoteAgent("laguna", "nvidia", "Laguna XS", "unknown", model_id="laguna-xs-2.1"),
            m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown", model_id="nvidia-a"),
            m.RemoteAgent("arctic", "nvidia", "Arctic Embed", "unknown", model_id="arctic-embed-l"),
        ]
        with mock.patch.dict(os.environ, {"LA_MEMORY_BUDGET_GB": "100"}):
            sp._VERDICT_MEMO.clear()
            for a in agents:   # what the app's _loaded does for kind == "remote"
                a.local_capable = sp.catalog_local_capable(a.provider, a.model_id)
        s = m.Settings()
        s.catalog_local_capable = sp.catalog_local_capable
        scr = m.RemoteScreen(s, agents=agents)
        ids = [i.id for g in scr.accordion.groups for i in g.items]
        self.assertEqual(ids, ["nvidia-a"], f"capable pinned rows hidden by default: {ids}")


if __name__ == "__main__":
    unittest.main()
