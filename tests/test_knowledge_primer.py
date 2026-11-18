#!/usr/bin/env python3
"""hooks/knowledge-primer.py: the 🧠 free-lane knowledge section is injected at SessionStart,
read from PRIMER via AST (the hook file is never executed by the nudge), and fail-open.

RED first: without knowledge_section() wired into offload-nudge.py, the nudge output contains no
"free-lane knowledge" section. Usage: python3 -m pytest tests/test_knowledge_primer.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "offload-nudge.py"
KNOWLEDGE = ROOT / "hooks" / "knowledge-primer.py"


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class KnowledgePrimerTests(unittest.TestCase):
    def test_primer_constant_exists_and_is_multiline(self):
        mod = load_mod(KNOWLEDGE, "knowledge_primer")
        self.assertTrue(mod.PRIMER.startswith("🧠"))
        self.assertGreaterEqual(len(mod.PRIMER.splitlines()), 5)

    def test_section_loads_primer_via_ast_without_executing_hook(self):
        mod = load_mod(HOOK, "nudge_kp")
        text = mod.knowledge_section()
        self.assertIn("🧠 free-lane knowledge", text)
        self.assertIn("watchdog", text)
        self.assertIn("la-evict", text)

    def test_nudge_output_carries_the_section_on_free_and_paid(self):
        for base in ("http://127.0.0.1:4141", "https://gateway.example.com"):
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_BASE_URL"}
            env["ANTHROPIC_BASE_URL"] = base
            env["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
            env["HOME"] = tempfile.mkdtemp(prefix="kp-home-")
            r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True,
                               env=env, stdin=subprocess.DEVNULL, timeout=15)
            self.assertEqual(r.returncode, 0, r.stderr)
            ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("🧠 free-lane knowledge", ctx, base)

    def test_section_fails_open_when_notes_file_missing(self):
        mod = load_mod(HOOK, "nudge_kp2")
        import unittest.mock as mock
        with mock.patch.object(mod, "KNOWLEDGE_HOOKS", ("does-not-exist.py",)):
            self.assertEqual(mod.knowledge_section(), "")


if __name__ == "__main__":
    unittest.main()
