#!/usr/bin/env python3
"""Task 10 Step 1 — the dispatch ladder's capacity-weighted spread (C7). RED first.

rank_entries: weighted random draw WITHOUT replacement, weight = pref_weight(rank) *
headroom(provider); ineligible (max_output < need_output) dropped; headroom 0 moves to the
END (still reachable — it queues), never dropped.
"""
import importlib.util
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
_spec = importlib.util.spec_from_file_location("free_agent_tool", REPO / "bin" / "free-agent-tool.py")
fat = importlib.util.module_from_spec(_spec)
sys.modules["free_agent_tool"] = fat
_spec.loader.exec_module(fat)
_spec2 = importlib.util.spec_from_file_location("provider_limits", REPO / "bin" / "provider_limits.py")
pl = importlib.util.module_from_spec(_spec2)
sys.modules["provider_limits"] = pl
_spec2.loader.exec_module(pl)

LIMITS_DOC = {
    "schema_version": 1, "session_min_context": 262145, "large_context_threshold": 262144,
    "providers": {
        "nvidia": {"rpm": 40, "tpm": None, "rpd": None, "rpw": None, "tpd": None,
                   "dispatch_only": False, "reserves_on_request": False,
                   "measured_at": "t", "source": "t"},
        "groq": {"rpm": 1000, "tpm": 8000, "rpd": None, "rpw": None, "tpd": None,
                 "dispatch_only": False, "reserves_on_request": False,
                 "measured_at": "t", "source": "t"},
        "gemini": {"rpm": None, "tpm": None, "rpd": 100, "rpw": None, "tpd": None,
                   "dispatch_only": True, "reserves_on_request": False,
                   "measured_at": "t", "source": "t"},
    },
    "models": {
        "nvidia:z-ai/glm-5.3-flash": {"context": 1000000, "max_output": 16384,
                                      "released": None, "broken": False,
                                      "source": "hf", "reason": "released unknown"},
        "groq:qwen/qwen3-32b": {"context": 131072, "max_output": 8192,
                                "released": None, "broken": False,
                                "source": "catalog", "reason": "released unknown"},
        "gemini:gemini-3.8-flash": {"context": 1048576, "max_output": 65536,
                                    "released": None, "broken": False,
                                    "source": "catalog", "reason": "released unknown"},
    },
}


def entries(*ids):
    out = []
    for i, (eid, prov, model) in enumerate(ids):
        out.append({"id": eid, "kind": "remote", "provider": prov, "model": model})
    return out


class RankEntriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = Path(self.tmp.name) / "provider-limits.json"
        p.write_text(json.dumps(LIMITS_DOC))
        self.limits = pl.load(p)
        self.ents = entries(("a", "nvidia", "z-ai/glm-5.3-flash"),
                           ("b", "groq", "qwen/qwen3-32b"),
                           ("c", "gemini", "gemini-3.8-flash"))

    def rank(self, ents, headroom, need_output=0, seed=42):
        return fat.rank_entries(ents, need_context=0, need_output=need_output,
                                limits=self.limits, headroom=headroom,
                                rng=random.Random(seed))

    def test_distribution_matches_weights_within_3pct(self):
        # all headrooms 1.0: weights are pref_weight(rank) = 1, 1/2, 1/3
        counts = {"a": 0, "b": 0, "c": 0}
        n = 10000
        for s in range(n):
            out = self.rank([*self.ents], headroom=lambda p: 1.0, seed=s)
            counts[out[0]["id"]] += 1
        total_w = 1.0 + 0.5 + 1 / 3
        for eid, w in (("a", 1.0), ("b", 0.5), ("c", 1 / 3)):
            expect = n * w / total_w
            self.assertAlmostEqual(counts[eid], expect, delta=0.03 * n,
                                   msg=f"{eid}: {counts[eid]} vs {expect:.0f}")

    def test_max_output_below_need_is_dropped(self):
        out = self.rank(self.ents, headroom=lambda p: 1.0, need_output=10000)
        # groq qwen3-32b max_output 8192 < 10000 -> dropped entirely
        self.assertNotIn("b", [e["id"] for e in out])
        self.assertEqual([e["id"] for e in out], ["a", "c"])

    def test_headroom_zero_moves_to_end_not_dropped(self):
        out = self.rank(self.ents, headroom=lambda p: 0.0 if p == "nvidia" else 1.0)
        ids = [e["id"] for e in out]
        self.assertEqual(ids[-1], "a", "headroom-0 nvidia entry last, still present")
        self.assertIn("a", ids)

    def test_seeded_rng_is_deterministic(self):
        def h(p):
            return 1.0
        a = self.rank(self.ents, h, seed=7)
        b = self.rank(self.ents, h, seed=7)
        self.assertEqual([e["id"] for e in a], [e["id"] for e in b])

    def test_weight_uses_headroom(self):
        # nvidia headroom 0.1 vs groq 1.0: groq should lead most draws despite rank
        counts = {"a": 0, "b": 0}
        ents = self.ents[:2]
        for s in range(2000):
            out = self.rank(ents, headroom=lambda p: 0.1 if p == "nvidia" else 1.0, seed=s)
            counts[out[0]["id"]] += 1
        self.assertGreater(counts["b"], counts["a"] * 3, counts)


if __name__ == "__main__":
    unittest.main()
