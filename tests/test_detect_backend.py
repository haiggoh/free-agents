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
import struct
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


def _safetensors(folder: Path, name: str, keys) -> Path:
    """A minimal safetensors file: 8-byte little-endian header length, then the JSON header.

    Only the header is ever read (that is all an MLX loader consults before mapping keys onto
    modules), so the data block can be a stub.
    """
    header = {k: {"dtype": "F16", "shape": [1]} for k in keys}
    header["__metadata__"] = {"format": "pt"}
    blob = json.dumps(header).encode()
    path = folder / name
    path.write_bytes(struct.pack("<Q", len(blob)) + blob + b"\0" * 16)
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

    # --- weight format: architecture support is necessary, NOT sufficient --------------------
    # Measured 2026-10-25: ~/.models/deepseek-v4-flash-0731-coder declares model_type=deepseek_v4
    # (which oMLX's mlx_vlm CAN build) AND weight_format="mxtq" (a 2-bit MoEspresso TurboQuant
    # bundle only the MoEspresso runtime reads). oMLX 0.7.1 loads 53 GB of weights and then dies
    # at load_weights(strict=True): "Received 46 parameters not in model: embed.biases,
    # embed.scales, head.scales, model.layers.N.ffn.experts.tq_bundle". The old verdict said
    # `omlx` — architecture-correct, format-wrong — so csl offered a model that cannot launch and
    # the session surfaced a 409 "unavailable after a previous load failure" instead of a cause.
    def test_foreign_weight_format_is_not_launched(self):
        d = self.folder("dsv4-mxtq", "deepseek_v4")
        (d / "config.json").write_text(json.dumps(
            {"model_type": "deepseek_v4", "weight_format": "mxtq",
             "mxtq_bits": 2, "expert_dtype": "fp4"}))
        v = self.verdicts(d, env_extra={"LA_MOESPRESSO_BIN": ""})["dsv4-mxtq"]
        self.assertEqual(v["backend"], "unsupported")
        self.assertIn("mxtq", v["reason"])

    def test_bundle_keys_in_the_weights_are_foreign_too(self):
        # A config whose weight_format was stripped still betrays itself in the shard header:
        # *.tq_bundle is a custom-runtime bundle, not an MLX quantization key. Note that plain
        # MLX quantized layers legitimately write *.scales / *.biases, so only the bundle suffix
        # is a signal — matching on scales/biases would flag every 4-bit model on this machine.
        d = self.folder("dsv4-bundle", "deepseek_v4")
        _safetensors(d, "model-00001-of-00002.safetensors",
                     ["model.layers.0.ffn.experts.tq_bundle", "model.layers.0.ffn.experts.weight"])
        v = self.verdicts(d, env_extra={"LA_MOESPRESSO_BIN": ""})["dsv4-bundle"]
        self.assertEqual(v["backend"], "unsupported")

    def test_plain_mlx_quantization_keys_stay_launchable(self):
        # The complement: ordinary MLX quantization (weight + scales + biases) must NOT be
        # flagged, or this guard would gate out every quantized model on the machine.
        d = self.folder("qwen-q4", "qwen3")
        _safetensors(d, "model.safetensors",
                     ["model.layers.0.self_attn.q_proj.weight",
                      "model.layers.0.self_attn.q_proj.scales",
                      "model.layers.0.self_attn.q_proj.biases"])
        v = self.verdicts(d)["qwen-q4"]
        self.assertEqual(v["backend"], "mlx")

    # --- MoEspresso: the artifact becomes launchable when a capable runtime is installed -------
    # The 0.44.2 gate refuses a foreign format outright, correct at the time: nothing on the
    # machine could read `mxtq`. With MoEspresso's 3.x line installed it CAN, so the verdict
    # moves from `unsupported` to `moespresso` — and, critically, a 4.x binary must NOT unlock
    # it (measured: 4.0.0 refuses the package with "unsupported required package feature").
    def test_moespresso_binary_makes_the_artifact_launchable(self):
        d = self.folder("dsv4-moespresso", "deepseek_v4")
        (d / "config.json").write_text(json.dumps(
            {"model_type": "deepseek_v4", "weight_format": "mxtq"}))
        stub = _exe(self.t / "moe-bin", "#!/bin/sh\necho 'moespresso 3.0.0'\n")
        v = self.verdicts(d, env_extra={"LA_MOESPRESSO_BIN": str(stub)})["dsv4-moespresso"]
        self.assertEqual(v["backend"], "moespresso")
        self.assertIn("mxtq", v["reason"])

    def test_moespresso_4x_is_not_a_capable_binary(self):
        """A 4.x MoEspresso cannot load the package, so it must not unlock the artifact."""
        d = self.folder("dsv4-moe4", "deepseek_v4")
        (d / "config.json").write_text(json.dumps(
            {"model_type": "deepseek_v4", "weight_format": "mxtq"}))
        stub = _exe(self.t / "moe4-bin", "#!/bin/sh\necho 'moespresso 4.0.0'\n")
        v = self.verdicts(d, env_extra={"LA_MOESPRESSO_BIN": str(stub)})["dsv4-moe4"]
        self.assertEqual(v["backend"], "unsupported")

    def test_moespresso_never_wins_for_an_ordinary_artifact(self):
        """MoEspresso serves packaged MoE models only; it must not capture plain MLX artifacts."""
        d = self.folder("qwen-with-moe", "qwen3")
        stub = _exe(self.t / "moe-bin2", "#!/bin/sh\necho 'moespresso 3.0.0'\n")
        v = self.verdicts(d, env_extra={"LA_MOESPRESSO_BIN": str(stub)})["qwen-with-moe"]
        self.assertEqual(v["backend"], "mlx")

    def test_gate_lets_an_moespresso_servable_artifact_through(self):
        """The launch guard must NOT refuse what MoEspresso can serve, once it is installed."""
        d = self.folder("dsv4-gate", "deepseek_v4")
        (d / "config.json").write_text(json.dumps(
            {"model_type": "deepseek_v4", "weight_format": "mxtq"}))
        stub = _exe(self.t / "moe-bin3", "#!/bin/sh\necho 'moespresso 3.0.0'\n")
        r = self.run_tool("--gate", str(d), env_extra={"LA_MOESPRESSO_BIN": str(stub)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "")
        # ...and still refuses it with no MoEspresso present (explicit empty pin = "none",
        # so the assertion cannot silently change when this machine gains a runtime).
        r2 = self.run_tool("--gate", str(d), env_extra={"LA_MOESPRESSO_BIN": ""})
        self.assertEqual(r2.returncode, 3)
        self.assertIn("mxtq", r2.stdout)

    def test_gguf_still_wins_before_the_format_check(self):
        # Order matters: a GGUF artifact gets llama_cpp regardless of any weight_format key.
        d = self.folder("dsv4-gguf", "deepseek_v4")
        (d / "config.json").write_text(json.dumps(
            {"model_type": "deepseek_v4", "weight_format": "mxtq"}))
        (d / "model-Q4_K_M.gguf").write_bytes(b"x")
        self.assertEqual(self.verdicts(d)["dsv4-gguf"]["backend"], "llama_cpp")

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
