#!/usr/bin/env python3
"""tests/test_glm53_retired_0493.py — 0.49.3: GLM 5.3 full (z-ai/glm-5.3) retired from NVIDIA.

EOL 2026-10-28T08:00:00Z (410 detail from a user session, 2026-10-31); live catalog check
2026-10-31 (remote-session.sh --models nvidia-mistral-large4, GET only) no longer lists
z-ai/glm-5.3 — only z-ai/glm-5.3-flash remains. The Kimi K3 mechanism is reused: the aliases
are hidden via config/broken-nvidia-models.json (--show-broken / menu key u reveals them).
Each test names what breaks it."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BROKEN = ROOT / "config" / "broken-nvidia-models.json"
REGISTRY_YAML = ROOT / "config" / "remote-registry.yaml"

ROW_RE = re.compile(r"- alias: (\S+)\n\s+provider: (\S+)\n\s+model_id: (\S+)")


def registry_rows():
    return [(alias, provider, model_id)
            for alias, provider, model_id in ROW_RE.findall(REGISTRY_YAML.read_text())]


def broken_aliases():
    return {m["alias"] for m in json.loads(BROKEN.read_text())["models"]}


class BrokenListTests(unittest.TestCase):
    def test_both_glm_full_aliases_listed_as_end_of_life(self):
        models = json.loads(BROKEN.read_text())["models"]
        entries = [m for m in models if m.get("model_id") == "z-ai/glm-5.3"]
        self.assertEqual({e["alias"] for e in entries},
                         {"nvidia-glm53-full", "nvidia-glm53-full-thinking"},
                         "both GLM-full aliases must be in broken-nvidia-models.json")
        for e in entries:
            self.assertEqual(e["failure_mode"], "end_of_life")
            self.assertIn("2026-10-28T08:00:00Z", e["reason"],
                          "the EOL date from the 410 detail must be recorded")

    def test_every_registry_row_for_the_eol_id_is_hidden(self):
        # a row for a retired id may exist (RETIRED note, like Kimi K3) but must be hidden
        glm_full = {a for a, _, mid in registry_rows() if mid == "z-ai/glm-5.3"}
        self.assertEqual(glm_full, {"nvidia-glm53-full", "nvidia-glm53-full-thinking"})
        self.assertLessEqual(glm_full, broken_aliases(),
                             "a registry row for a retired id must be hidden via broken-nvidia-models.json")

    def test_flash_stays_visible(self):
        flash = {a for a, _, mid in registry_rows() if mid == "z-ai/glm-5.3-flash"}
        self.assertTrue(flash, "GLM 5.3 Flash rows must stay in the registry")
        self.assertFalse(flash & broken_aliases(),
                         "GLM 5.3 Flash is NOT retired — it must stay visible")


class CliListTests(unittest.TestCase):
    def run_list(self, extra_env=None, args=("--list",)):
        with tempfile.TemporaryDirectory() as td:
            keys = Path(td) / "keys"
            keys.mkdir()
            (keys / "nvidia").write_text("fake-key-for-tests\n")  # never touch real keys
            env = dict(os.environ, LA_API_KEYS_DIR=str(keys))
            env.update(extra_env or {})
            r = subprocess.run(["bash", str(ROOT / "bin/remote-session.sh"), *args],
                               env=env, text=True, capture_output=True, timeout=60,
                               stdin=subprocess.DEVNULL, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return r.stdout

    def test_list_hides_glm_full_but_shows_flash_and_le_chonk(self):
        out = self.run_list()
        self.assertNotIn("nvidia-glm53-full", out,
                         "retired GLM full (both aliases share the prefix) must be hidden by default")
        self.assertRegex(out, r"(?m)^\s*\d+\s+nvidia-glm53\b(?!-)",
                         "GLM 5.3 Flash (exact alias) must stay visible")
        self.assertIn("nvidia-mistral-large4", out, "Le Chonk must stay visible and lead")

    def test_show_broken_reveals_glm_full(self):
        out = self.run_list(args=("--show-broken", "--list"))
        self.assertIn("nvidia-glm53-full", out, "--show-broken must reveal the retired rows")

    def test_mutation_empty_broken_list_reveals_glm_full(self):
        # planted positive for the guard: with an empty broken file the rows must reappear,
        # proving the default-hide above is caused by the file, not by a hardcoded copy
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty.json"
            empty.write_text(json.dumps({"models": []}))
            out = self.run_list(extra_env={"LA_BROKEN_MODELS_FILE": str(empty)})
            self.assertIn("nvidia-glm53-full", out,
                          "LA_BROKEN_MODELS_FILE override must control hiding (fail-open on missing file)")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
    unittest.main()
