#!/usr/bin/env python3
"""tests/test_detect_backend.py — bin/la-detect-backend.py picks a model's backend from evidence.

The rule (user 2026-10-15): GGUF -> llama_cpp; otherwise the checkpoint's config.json model_type
decides — rapid-mlx when the PINNED rapid's own mlx_lm can build it (generic `mlx`, which resolves to
the machine default), oMLX when only oMLX's bundled mlx_lm / mlx_vlm can, else unsupported. Measured
that day: rapid 0.16.1 rejects `laguna` ("Model type laguna not supported") and oMLX serves it.

Every engine interpreter here is a STUB that prints a canned registry, so no mlx is imported and the
real venvs are never touched. The stubs are reached the same way the real ones are: via the shebang
of the engine binary (LA_RAPID_BIN / LA_OMLX_BIN), with the LA_DETECT_*_PY overrides tested too.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "bin" / "la-detect-backend.py"


def _exe(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class DetectBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.models = self.t / "models"
        self.models.mkdir()
        # rapid knows qwen3 natively and kimi_k2 only through MODEL_REMAPPING; oMLX additionally
        # knows laguna (mlx_lm) and deepseek_v4 (mlx_vlm).
        self.rapid_py = _exe(self.t / "rapid-python", "#!/bin/sh\n"
                             "echo '{\"mlx_lm\": [\"qwen3\", \"kimi_k2\"]}'\n")
        self.omlx_py = _exe(self.t / "omlx-python", "#!/bin/sh\n"
                            "echo '{\"mlx_lm\": [\"qwen3\", \"kimi_k2\", \"laguna\"], "
                            "\"mlx_vlm\": [\"deepseek_v4\"]}'\n")
        # Engine binaries whose shebang names the stub interpreter, like a real venv entry point.
        self.rapid_bin = _exe(self.t / "rapid-mlx", f"#!{self.rapid_py}\nimport sys\n")
        self.omlx_bin = _exe(self.t / "omlx", f"#!{self.omlx_py}\nimport sys\n")

    def folder(self, name, model_type=None, files=()):
        d = self.models / name
        d.mkdir()
        if model_type is not None:
            (d / "config.json").write_text(json.dumps({"model_type": model_type}))
        for f in files:
            (d / f).write_bytes(b"x")
        return d

    def run_tool(self, *args, env_extra=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LA_", "HOME"))}
        env.update(HOME=str(self.t), LA_RAPID_BIN=str(self.rapid_bin), LA_OMLX_BIN=str(self.omlx_bin))
        env.update(env_extra or {})
        return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                              env=env, timeout=60)

    def verdicts(self, *folders, env_extra=None):
        r = self.run_tool("--json", *map(str, folders), env_extra=env_extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {Path(v["folder"]).name: v for v in json.loads(r.stdout)}

    # --- the rule ---------------------------------------------------------------------------
    def test_architecture_rapid_supports_gets_the_generic_default(self):
        v = self.verdicts(self.folder("qwen", "qwen3"))["qwen"]
        self.assertEqual(v["backend"], "mlx")

    def test_remapped_model_type_counts_as_rapid_supported(self):
        v = self.verdicts(self.folder("kimi", "kimi_k2"))["kimi"]
        self.assertEqual(v["backend"], "mlx")

    def test_architecture_only_omlx_supports_is_pinned_to_omlx(self):
        v = self.verdicts(self.folder("laguna-s", "laguna"))["laguna-s"]
        self.assertEqual(v["backend"], "omlx")
        self.assertIn("laguna", v["reason"])

    def test_omlx_vision_registry_also_counts(self):
        v = self.verdicts(self.folder("dsv4", "deepseek_v4"))["dsv4"]
        self.assertEqual(v["backend"], "omlx")

    def test_gguf_goes_to_llama_cpp_before_any_model_type_check(self):
        v = self.verdicts(self.folder("q-gguf", "qwen3", files=("model-Q4_K_M.gguf",)))["q-gguf"]
        self.assertEqual(v["backend"], "llama_cpp")

    def test_architecture_nobody_supports_is_unsupported(self):
        v = self.verdicts(self.folder("alien", "alien_arch"))["alien"]
        self.assertEqual(v["backend"], "unsupported")
        self.assertIn("alien_arch", v["reason"])

    def test_speculative_drafter_sidecar_is_not_a_model(self):
        # Qwen3.8-27B-MTP-4bit (228 MB, model_type qwen3_5_mtp) is the DRAFTER qwen-3.8-operator
        # loads through its rapid_spec_json, not a session model. oMLX's own rule: a model_type
        # ending in _mtp or _assistant marks a speculative-decoding helper.
        for name, mt in (("q-mtp", "qwen3_5_mtp"), ("g-asst", "gemma4_assistant")):
            v = self.verdicts(self.folder(name, mt))[name]
            self.assertEqual(v["backend"], "drafter", mt)

    def test_missing_config_is_unknown_not_a_guess(self):
        v = self.verdicts(self.folder("empty"))["empty"]
        self.assertEqual(v["backend"], "unknown")

    # --- discriminants: the verdict follows the ENGINE's registry, not a list in this tool ---
    def test_rapid_learning_an_architecture_moves_it_back_to_the_default(self):
        rapid_new = _exe(self.t / "rapid-python-new", "#!/bin/sh\n"
                         "echo '{\"mlx_lm\": [\"qwen3\", \"laguna\"]}'\n")
        v = self.verdicts(self.folder("laguna-s", "laguna"),
                          env_extra={"LA_DETECT_RAPID_PY": str(rapid_new)})["laguna-s"]
        self.assertEqual(v["backend"], "mlx")

    def test_uninspectable_rapid_never_pins_omlx_for_a_common_architecture(self):
        # No rapid at all: a qwen model must NOT be pinned to omlx merely because oMLX could load
        # it — that would turn oMLX into a de-facto default. Only rapid's absence of evidence.
        v = self.verdicts(self.folder("qwen", "qwen3"),
                          env_extra={"LA_RAPID_BIN": str(self.t / "missing")})["qwen"]
        self.assertEqual(v["backend"], "mlx")

    # --- CLI contract -----------------------------------------------------------------------
    def test_tsv_output_is_one_line_per_folder(self):
        a, b = self.folder("qwen", "qwen3"), self.folder("laguna-s", "laguna")
        r = self.run_tool(str(a), str(b))
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [line.split("\t") for line in r.stdout.splitlines()]
        self.assertEqual([(Path(x[0]).name, x[1]) for x in rows], [("qwen", "mlx"), ("laguna-s", "omlx")])

    def test_help_and_unknown_flag(self):
        self.assertEqual(self.run_tool("--help").returncode, 0)
        r = self.run_tool("--bogus")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_no_folders_is_a_usage_error(self):
        self.assertEqual(self.run_tool().returncode, 2)


if __name__ == "__main__":
    unittest.main()
