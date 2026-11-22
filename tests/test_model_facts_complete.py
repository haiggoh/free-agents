#!/usr/bin/env python3
"""Task 4 Step 1 — model-facts completeness (spec C10 release gate): every alias in
`remote-session.sh --inventory` whose provider has a key name in the bin/ provider table
must have provider-limits facts in which EVERY filter field (context, broken, released) is
a value or an explicit null-with-reason. A MISSING entry fails, naming the alias.
"""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
_spec = importlib.util.spec_from_file_location("provider_limits", REPO / "bin" / "provider_limits.py")
pl = importlib.util.module_from_spec(_spec)
sys.modules["provider_limits"] = pl          # dataclasses need the module registered
_spec.loader.exec_module(pl)
import remote_provider_core as rpc  # noqa: E402


def inventory():
    """(alias, provider, model_id) triples from remote-session.sh --inventory."""
    out = subprocess.run(["bash", str(REPO / "bin" / "remote-session.sh"), "--inventory"],
                         capture_output=True, text=True, timeout=300, check=True).stdout
    rows = []
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) >= 8 and cols[1] in _providers_with_keys():
            rows.append((cols[0], cols[1], cols[-1]))
    return rows


def _providers_with_keys():
    return {pid for pid, p in rpc.PROVIDERS.items() if p.key_name}


class ModelFactsCompleteTests(unittest.TestCase):
    def test_every_keyed_roster_model_has_complete_facts(self):
        limits = pl.load()
        rows = inventory()
        self.assertTrue(rows, "inventory produced no rows")
        missing, nulls_without_reason = [], []
        for alias, provider, model_id in rows:
            facts = limits.model(provider, model_id)
            if facts is None:
                missing.append(f"{alias} ({provider}:{model_id})")
                continue
            for field, value in (("context", facts.context), ("broken", facts.broken),
                                 ("released", facts.released)):
                if value is None and not facts.reason:
                    nulls_without_reason.append(f"{alias}: {field} null without reason")
        self.assertEqual(missing, [], "aliases with NO facts entry")
        self.assertEqual(nulls_without_reason, [], "null fields without a reason")

    def test_glm53_flash_context_is_a_value(self):
        limits = pl.load()
        facts = limits.model("nvidia", "z-ai/glm-5.3-flash")
        self.assertIsNotNone(facts, "the session's own model must have facts")
        self.assertIsInstance(facts.context, int, "GLM 5.3 Flash context (1M, user-observed)")


if __name__ == "__main__":
    unittest.main()