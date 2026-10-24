#!/usr/bin/env python3
"""bin/model_names.py and the roster's curated names (user 2026-10-22).

A model row names the model's MAKER, never the API provider it is served from: the picker's
group header already says the provider, and only the Nemotron models are actually NVIDIA's.
Catalog ids are turned into readable names (capitals and spaces, no kebab-case); the curated
roster names follow the same rule, which the drift check below holds them to.

Usage: python3 tests/test_model_names.py [-v]
"""
from pathlib import Path
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import model_names as n  # noqa: E402


class Readable(unittest.TestCase):
    CASES = {
        "deepseek-ai/deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
        "z-ai/glm-5.3": "Z.ai GLM 5.3",
        "nvidia/nemotron-3-ultra-550b-a55b": "NVIDIA Nemotron 3 Ultra 550B-A55B",
        "@cf/qwen/qwen3.8-27b": "Alibaba Qwen 3.8 27B",
        "openai/gpt-oss-20b": "OpenAI GPT-OSS 20B",
        "gpt-oss-120b": "OpenAI GPT-OSS 120B",            # no vendor segment: by family
        "gemini-3.8-flash": "Google Gemini 3.8 Flash",
        "moonshotai/kimi-k2.6": "Moonshot Kimi K2.6",
        "anthropic/claude-sonnet-4-6": "Anthropic Claude Sonnet 4.6",
        "openai/gpt-5.4-nano": "OpenAI GPT-5.4 Nano",
        "nvidia/nemotron-3-super-120b-a12b:free": "NVIDIA Nemotron 3 Super 120B-A12B (free)",
        "gemma4:31b": "Google Gemma 4 31B",
        "z-ai/glm-4.5v": "Z.ai GLM 4.5V",
        "mistral-medium-2604": "Mistral Medium 2604",
        "stepfun-ai/step-5-preview": "StepFun Step 5 Preview",   # the maker leads; NVIDIA only serves it
        "someone/odd-model-7b": "Odd Model 7B",           # unknown maker: no guessed prefix
        # Local profile/quant tokens read as brand + bits (user 2026-10-23, from the model
        # folders' own quantization configs: oq6e/oq8e/oq6 = OptiQ 6/8-bit, 6xl = 6-bit XL).
        "kat-coder-oq6e": "Kwaipilot KAT Coder OptiQ 6-bit",
        "kat-coder-oq8e": "Kwaipilot KAT Coder OptiQ 8-bit",
        "kat-coder-6xl": "Kwaipilot KAT Coder 6-bit XL",
        "gemma-4-31b-optiq": "Google Gemma 4 31B OptiQ",
        "qwen-3.8-oq6": "Alibaba Qwen 3.8 OptiQ 6-bit",
        "agents-a1-kimi-bf16": "Agents A1 Kimi BF16",
        # The instruct (-it) and format (-MLX) suffixes are dropped: the row already says the
        # backend, and every chat model is instruct-tuned (user-approved preview 2026-10-23).
        # Size + active params keep the house hyphen convention (26B-A4B, like 550B-A55B).
        "gemma-4-26b-a4b-it-MLX-8bit": "Google Gemma 4 26B-A4B 8-bit",
    }

    def test_cases(self):
        for mid, want in self.CASES.items():
            with self.subTest(mid):
                self.assertEqual(n.readable(mid), want)

    def test_no_kebab_case_in_real_catalog_shapes(self):
        for mid in self.CASES:
            self.assertNotRegex(n.readable(mid).split(" (")[0], r"[a-z]-[a-z]")

    def test_cli_help_and_unknown_flag(self):
        r = subprocess.run([sys.executable, str(ROOT / "bin/model_names.py"), "--help"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)
        r = subprocess.run([sys.executable, str(ROOT / "bin/model_names.py"), "--nope"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")


class CuratedNames(unittest.TestCase):
    def test_no_curated_name_restates_the_provider(self):
        reg = json.loads((ROOT / "config/remote-registry.json").read_text())
        providers = reg["providers"]
        for e in reg["entries"]:
            if e["model_id"] == "SELECT":
                continue
            prov = providers[e["provider"]]["display"].split()[0]          # "NVIDIA", "Groq"
            maker = n.manufacturer(e["model_id"])
            with self.subTest(e["alias"]):
                if maker.lower() != prov.lower():
                    self.assertFalse(e["display"].lower().startswith(prov.lower() + " "),
                                     f'{e["display"]!r}: the group header already names {prov}')
                if maker:
                    self.assertTrue(e["display"].startswith(maker),
                                    f'{e["display"]!r} should lead with its maker {maker}')


if __name__ == "__main__":
    unittest.main()
