#!/usr/bin/env python3
"""free-agent-tool.py must map a model-specific subdir to its provider by prefix.

The subdir names the model ("nvidia-nemotron-550b"); before 0.25.5 only exact provider names
matched, so every such binding was dispatched to the OpenRouter fallback. subprocess.run is
replaced with a recorder, so nothing is sent anywhere.
"""
import importlib.util
import subprocess
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "bin" / "free-agent-tool.py"
spec = importlib.util.spec_from_file_location("free_agent_tool", TOOL)
fat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fat)


class ProviderMapping(unittest.TestCase):
    def provider_for(self, subdir):
        seen = {}

        def fake_run(cmd, **_kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        real, fat.subprocess.run = fat.subprocess.run, fake_run
        try:
            fat.execute_api_dispatch("a", "p", subdir, "some/model")
        finally:
            fat.subprocess.run = real
        cmd = seen["cmd"]
        return cmd[cmd.index("--provider") + 1]

    def test_exact_provider(self):
        self.assertEqual(self.provider_for("nvidia"), "nvidia")

    def test_model_specific_subdir_uses_prefix(self):
        self.assertEqual(self.provider_for("nvidia-nemotron-550b"), "nvidia")
        self.assertEqual(self.provider_for("groq-oss120"), "groq")

    def test_unknown_still_falls_back_to_openrouter(self):
        self.assertEqual(self.provider_for("mystery-model"), "openrouter")


if __name__ == "__main__":
    unittest.main()
