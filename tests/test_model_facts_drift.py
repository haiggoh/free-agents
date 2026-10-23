#!/usr/bin/env python3
"""The JSON files that NAME models must agree with the single source of truth.

config/remote-registry.yaml holds the objective model/provider facts. Three hand-kept JSON files
refer to its entries and are NOT generated from it (maintainer preferences and field notes stay
separate on purpose, user 2026-10-21), so this test is what stops them drifting:

  config/broken-nvidia-models.json   every alias exists in the YAML, with the same model id
  config/remote-model-notes.json     every alias key exists in the YAML
  config/dispatch-preferences.json   every remote provider/model exists in the YAML; every local
                                     alias is a la_register alias; nothing hidden as broken is on
                                     a dispatch or fallback list

Usage: python3 tests/test_model_facts_drift.py [-v]   (needs pyyaml: run under the LiteLLM venv)
"""
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def load(rel):
    return json.loads((ROOT / rel).read_text())


@unittest.skipIf(yaml is None, "pyyaml not installed (run under the LiteLLM venv)")
class ModelFactsDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reg = yaml.safe_load((ROOT / "config/remote-registry.yaml").read_text())
        cls.entries = {e["alias"]: e for e in reg["entries"]}
        cls.ids = {(e["provider"], e["model_id"]) for e in reg["entries"]}
        cls.providers = set(reg["providers"])
        # The registered LOCAL aliases, read the way the launchers read them (the live-config
        # resolution in config-lib.sh), from a probe with the ambient overrides STRIPPED —
        # an xdist worker can carry a leaked LA_CONFIG_DIR from an unrelated test module.
        # Reading the tree's own files made this test worktree-state-dependent: a fresh
        # worktree judged the user's personal aliases against the 9-model example roster
        # (gate run 2026-10-23). When only example defaults are available there is no live
        # registry to judge against, so the guard skips judgment (empty set).
        probe = subprocess.run(
            ["env", "-u", "LA_CONFIG_DIR", "-u", "LA_LIVE_CONFIG_DIR", "-u", "LA_USER_STATE_DIR",
             "bash", "-c",
             '. "$1" >/dev/null 2>&1; la_load_config >/dev/null 2>&1; '
             'printf "%s\\t%s\\t%s" "${LA_ALIASES[*]:-}" "${LA_FALLBACK_CONFIG:-0}" "${LA_CONFIG_SOURCE:-}"',
             "probe", str(ROOT / "config/config-lib.sh")],
            capture_output=True, text=True)
        cls.local_aliases: set[str] = set()
        if probe.returncode == 0:
            aliases, fallback, _source = (probe.stdout.split("\t", 2) + ["", "0", ""])[:3]
            if fallback.strip() != "1":
                cls.local_aliases = set(aliases.split())

    def test_broken_list_names_yaml_entries(self):
        for m in load("config/broken-nvidia-models.json")["models"]:
            with self.subTest(alias=m.get("alias")):
                self.assertTrue(m["alias"] in self.entries, f"{m['alias']}: alias not in remote-registry.yaml")
                self.assertEqual(m["model_id"], self.entries[m["alias"]]["model_id"])

    def test_model_notes_name_yaml_entries(self):
        for alias in load("config/remote-model-notes.json"):
            if alias.startswith("_"):
                continue
            with self.subTest(alias=alias):
                self.assertTrue(alias in self.entries, f"{alias}: alias not in remote-registry.yaml")

    def test_dispatch_and_fallback_models_exist(self):
        prefs = load("config/dispatch-preferences.json")
        for e in prefs["order"] + prefs.get("session_fallbacks", {}).get("order", []):
            with self.subTest(entry=e.get("id") or e.get("model")):
                if e.get("kind") == "local":
                    if self.local_aliases:          # a checkout without config files cannot judge
                        self.assertTrue(e["alias"] in self.local_aliases, f"{e['alias']}: not a la_register alias")
                    continue
                self.assertTrue(e["provider"] in self.providers, f"{e['provider']}: provider not in the YAML")
                self.assertTrue((e["provider"], e["model"]) in self.ids, f"{e['provider']}:{e['model']}: not in the YAML")

    def test_nothing_hidden_as_broken_is_recommended(self):
        broken = {m["model_id"] for m in load("config/broken-nvidia-models.json")["models"]}
        prefs = load("config/dispatch-preferences.json")
        for e in prefs["order"] + prefs.get("session_fallbacks", {}).get("order", []):
            if e.get("kind") != "local":
                with self.subTest(model=e["model"]):
                    self.assertFalse(e["model"] in broken, f"{e['model']}: hidden as broken but recommended")


if __name__ == "__main__":
    unittest.main()
