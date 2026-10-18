"""Tests for remote provider registry generation and runtime behavior."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "bin" / "remote-registry-generate.py"

_spec = importlib.util.spec_from_file_location("rrg", GENERATOR_PATH)
assert _spec and _spec.loader, "Failed to load generator module spec"
rrg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rrg)


def _has_pyyaml() -> bool:
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


def _get_bash() -> str:
    if os.path.exists("/bin/bash"):
        return "/bin/bash"
    bash = shutil.which("bash")
    if bash:
        return bash
    return "bash"


class TestRemoteRegistryGenerated(unittest.TestCase):
    def test_drift(self) -> None:
        """Committed artifacts must match generator output exactly."""
        if not _has_pyyaml():
            self.skipTest("pyyaml not installed")
        yaml_path = ROOT / "config" / "remote-registry.yaml"
        data = rrg.load(yaml_path)
        artifacts = rrg.render(data)

        committed_json = (ROOT / "config" / "remote-registry.json").read_bytes()
        committed_sh = (ROOT / "bin" / "remote-provider-registry.sh").read_bytes()

        msg = "Committed artifacts drift from YAML. Run bin/remote-registry-generate.py --write"
        self.assertEqual(committed_json, artifacts["config/remote-registry.json"].encode("utf-8"), msg)
        self.assertEqual(committed_sh, artifacts["bin/remote-provider-registry.sh"].encode("utf-8"), msg)

    def test_check_cli(self) -> None:
        """--check succeeds on repo and detects drift when YAML changes."""
        if not _has_pyyaml():
            self.skipTest("pyyaml not installed")
        res = subprocess.run(
            [sys.executable, str(GENERATOR_PATH), "--check"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(res.returncode, 0, f"--check failed: {res.stderr}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_yaml = Path(tmpdir) / "remote-registry.yaml"
            yaml_content = (ROOT / "config" / "remote-registry.yaml").read_text(encoding="utf-8")
            # Modify siliconflow base_url line
            modified = yaml_content.replace(
                "https://api.siliconflow.com/v1",
                "https://api.siliconflow.com/v2-modified",
            )
            self.assertNotEqual(yaml_content, modified)
            tmp_yaml.write_text(modified, encoding="utf-8")

            res_drift = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR_PATH),
                    "--check",
                    "--yaml",
                    str(tmp_yaml),
                    "--root",
                    str(ROOT),
                ],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            self.assertEqual(res_drift.returncode, 1)
            self.assertIn("config/remote-registry.json", res_drift.stderr)
            self.assertIn("bin/remote-provider-registry.sh", res_drift.stderr)

    def test_write_cli(self) -> None:
        """--write writes artifacts, sets permissions, and preserves mtimes if unchanged."""
        if not _has_pyyaml():
            self.skipTest("pyyaml not installed")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            (tmp_root / "config").mkdir()
            (tmp_root / "bin").mkdir()

            res1 = subprocess.run(
                [sys.executable, str(GENERATOR_PATH), "--write", "--root", str(tmp_root)],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            self.assertEqual(res1.returncode, 0, res1.stderr)

            json_file = tmp_root / "config" / "remote-registry.json"
            sh_file = tmp_root / "bin" / "remote-provider-registry.sh"
            self.assertTrue(json_file.is_file())
            self.assertTrue(sh_file.is_file())

            sh_mode = stat.S_IMODE(sh_file.stat().st_mode)
            self.assertEqual(sh_mode, 0o755)
            json_mode = stat.S_IMODE(json_file.stat().st_mode)
            self.assertEqual(json_mode, 0o644)

            sh_mtime1 = sh_file.stat().st_mtime_ns
            json_mtime1 = json_file.stat().st_mtime_ns

            time.sleep(0.05)
            res2 = subprocess.run(
                [sys.executable, str(GENERATOR_PATH), "--write", "--root", str(tmp_root)],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            self.assertEqual(res2.returncode, 0)
            self.assertEqual(sh_file.stat().st_mtime_ns, sh_mtime1)
            self.assertEqual(json_file.stat().st_mtime_ns, json_mtime1)

            res_chk = subprocess.run(
                [sys.executable, str(GENERATOR_PATH), "--check", "--root", str(tmp_root)],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            self.assertEqual(res_chk.returncode, 0)

            # Append line to tmp .sh
            with sh_file.open("a", encoding="utf-8") as f:
                f.write("\n# drift comment\n")

            res_chk_drift = subprocess.run(
                [sys.executable, str(GENERATOR_PATH), "--check", "--root", str(tmp_root)],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            self.assertEqual(res_chk_drift.returncode, 1)
            self.assertIn("bin/remote-provider-registry.sh", res_chk_drift.stderr)

    def test_behaviour_reference_table(self) -> None:
        """Bash execution behavior of committed library matches reference table."""
        bash = _get_bash()
        with tempfile.TemporaryDirectory() as tmpdir:
            stub_dir = Path(tmpdir)
            curl_stub = stub_dir / "curl"
            curl_stub.write_text('#!/usr/bin/env bash\necho "URL ${@: -1}"\nexit 0\n', encoding="utf-8")
            curl_stub.chmod(0o755)

            keys_stub = stub_dir / "keys"
            keys_stub.write_text(
                '#!/usr/bin/env bash\n'
                'prov="$2"\n'
                'if [[ "$prov" == "cloudflare" ]]; then\n'
                '    printf "CLOUDFLARE_API_TOKEN=secret\\nCLOUDFLARE_ACCOUNT_ID=0123456789abcdef0123456789abcdef\\n"\n'
                'else\n'
                '    printf "K_%s=secret\\n" "$prov"\n'
                'fi\n',
                encoding="utf-8",
            )
            keys_stub.chmod(0o755)

            sh_lib = ROOT / "bin" / "remote-provider-registry.sh"

            test_script = f"""
export PATH="{stub_dir}:$PATH"
KEYS="{keys_stub}"
_cloudflare_base() {{
    printf 'https://api.cloudflare.com/client/v4/accounts/%s/ai/v1' 0123456789abcdef0123456789abcdef
}}
source "{sh_lib}"

providers=(cerebras cloudflare gemini github groq kilo llm7 mistral modelscope nvidia openrouter sambanova siliconflow streamlake vercel zai bogus)

for p in "${{providers[@]}}"; do
    out="$( _litellm_route "$p" m/x 2>&1 )"
    rc=$?
    if [[ -n "$out" ]]; then
        printf "route %s rc=%d %s\\n" "$p" "$rc" "$out"
    else
        printf "route %s rc=%d \\n" "$p" "$rc"
    fi

    out="$( _openai_base "$p" 2>&1 )"
    rc=$?
    if [[ -n "$out" ]]; then
        printf "base %s rc=%d %s\\n" "$p" "$rc" "$out"
    else
        printf "base %s rc=%d \\n" "$p" "$rc"
    fi

    out="$( _catalog_request "$p" 2>&1 )"
    rc=$?
    if [[ -n "$out" ]]; then
        printf "cat %s rc=%d %s\\n" "$p" "$rc" "$out"
    else
        printf "cat %s rc=%d \\n" "$p" "$rc"
    fi
done
"""
            res = subprocess.run(
                [bash, "-c", test_script],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)

            expected_table = """route cerebras rc=0 cerebras/m/x|
base cerebras rc=2 
cat cerebras rc=0 URL https://api.cerebras.ai/v1/models
route cloudflare rc=0 openai/m/x|https://api.cloudflare.com/client/v4/accounts/0123456789abcdef0123456789abcdef/ai/v1
base cloudflare rc=2 
cat cloudflare rc=0 URL https://api.cloudflare.com/client/v4/accounts/0123456789abcdef0123456789abcdef/ai/models/search?per_page=100
route gemini rc=0 gemini/m/x|
base gemini rc=2 
cat gemini rc=0 URL https://generativelanguage.googleapis.com/v1beta/openai/models
route github rc=1 
base github rc=2 
cat github rc=2 remote-session: GitHub Models is retired (official docs; catalog HTTP 410 checked 2026-09-14). Select another provider directly; no generation or automatic fallback attempted. https://docs.github.com/en/github-models
route groq rc=0 groq/m/x|
base groq rc=2 
cat groq rc=0 URL https://api.groq.com/openai/v1/models
route kilo rc=0 openai/m/x|https://api.kilo.ai/api/gateway
base kilo rc=0 https://api.kilo.ai/api/gateway
cat kilo rc=0 URL https://api.kilo.ai/api/gateway/models
route llm7 rc=0 openai/m/x|https://api.llm7.io/v1
base llm7 rc=0 https://api.llm7.io/v1
cat llm7 rc=0 URL https://api.llm7.io/v1/models
route mistral rc=0 mistral/m/x|
base mistral rc=0 https://api.mistral.ai/v1
cat mistral rc=0 URL https://api.mistral.ai/v1/models
route modelscope rc=0 openai/m/x|https://api-inference.modelscope.cn/v1
base modelscope rc=0 https://api-inference.modelscope.cn/v1
cat modelscope rc=2 remote-session: no documented ModelScope catalog route; use --remote-model ID from the API-Inference model page: https://modelscope.cn/docs/model-service/API-Inference/intro
route nvidia rc=0 nvidia_nim/m/x|
base nvidia rc=2 
cat nvidia rc=0 URL https://integrate.api.nvidia.com/v1/models
route openrouter rc=0 openrouter/m/x|
base openrouter rc=2 
cat openrouter rc=0 URL https://openrouter.ai/api/v1/models
route sambanova rc=0 openai/m/x|https://api.sambanova.ai/v1
base sambanova rc=0 https://api.sambanova.ai/v1
cat sambanova rc=0 URL https://api.sambanova.ai/v1/models
route siliconflow rc=0 openai/m/x|https://api.siliconflow.com/v1
base siliconflow rc=0 https://api.siliconflow.com/v1
cat siliconflow rc=0 URL https://api.siliconflow.com/v1/models?type=text
route streamlake rc=0 openai/m/x|https://api.streamlake.ai/v1
base streamlake rc=0 https://api.streamlake.ai/v1
cat streamlake rc=0 URL https://api.streamlake.ai/v1/models
route vercel rc=0 openai/m/x|https://ai-gateway.vercel.sh/v1
base vercel rc=0 https://ai-gateway.vercel.sh/v1
cat vercel rc=0 URL https://ai-gateway.vercel.sh/v1/models
route zai rc=0 openai/m/x|https://api.z.ai/api/paas/v4
base zai rc=0 https://api.z.ai/api/paas/v4
cat zai rc=2 remote-session: no documented ZAI catalog route; use --remote-model ID from https://docs.z.ai/guides/overview . This route uses general API billing, not the Coding Plan.
route bogus rc=1 
base bogus rc=2 
cat bogus rc=2 remote-session: no documented catalog route for bogus; choose an API model from its provider docs with --remote-model ID. Catalog verification unavailable."""

            actual_lines = [line for line in res.stdout.strip().splitlines() if line]
            expected_lines = [line for line in expected_table.strip().splitlines() if line]
            self.assertEqual(actual_lines, expected_lines)

    def test_env_clear(self) -> None:
        """_clear_provider_env clears all registry vars and preserves GITHUB_TOKEN."""
        bash = _get_bash()
        sh_lib = ROOT / "bin" / "remote-provider-registry.sh"

        vars_to_clear = [
            "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
            "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "GITHUB_MODELS_TOKEN",
            "CEREBRAS_API_KEY", "NVIDIA_API_KEY", "MISTRAL_API_KEY", "ZAI_API_KEY",
            "SILICONFLOW_API_KEY", "LLM7_API_KEY", "KILO_API_KEY", "AI_GATEWAY_API_KEY",
            "SAMBANOVA_API_KEY", "MODELSCOPE_API_KEY", "STREAMLAKE_API_KEY", "OPENAI_API_KEY",
        ]

        exports = "\n".join(f'export {v}="x"' for v in vars_to_clear)
        checks = "\n".join(f'printf "%s=${{{v}:-unset}}\\n" "{v}"' for v in vars_to_clear)

        test_script = f"""
{exports}
export GITHUB_TOKEN="keep"
source "{sh_lib}"
_clear_provider_env
{checks}
printf "GITHUB_TOKEN=${{GITHUB_TOKEN:-unset}}\\n"
"""
        res = subprocess.run([bash, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        lines = res.stdout.strip().splitlines()

        expected = [f"{v}=unset" for v in vars_to_clear] + ["GITHUB_TOKEN=keep"]
        self.assertEqual(lines, expected)

    def test_sourcing_is_silent_and_cli_exit_codes(self) -> None:
        """Sourcing emits no output; direct CLI execution enforces help or returns 2."""
        bash = _get_bash()
        sh_lib = ROOT / "bin" / "remote-provider-registry.sh"

        res_source = subprocess.run(
            [bash, "-c", f'source "{sh_lib}"'],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_source.returncode, 0)
        self.assertEqual(res_source.stdout, "")
        self.assertEqual(res_source.stderr, "")

        res_help = subprocess.run([bash, str(sh_lib), "--help"], capture_output=True, text=True)
        self.assertEqual(res_help.returncode, 0)
        self.assertGreaterEqual(len(res_help.stdout.strip().splitlines()), 2)

        res_no_arg = subprocess.run([bash, str(sh_lib)], capture_output=True, text=True)
        self.assertEqual(res_no_arg.returncode, 2)
        self.assertIn("remote-provider-registry.sh: source this file; see --help", res_no_arg.stderr)

    def test_roster_parity(self) -> None:
        """Rendered YAML entries match LA_REMOTE_AGENTS in config/remote-agents.sh."""
        if not _has_pyyaml():
            self.skipTest("pyyaml not installed")
        yaml_path = ROOT / "config" / "remote-registry.yaml"
        sh_agents = ROOT / "config" / "remote-agents.sh"
        if not sh_agents.is_file():
            self.skipTest("config/remote-agents.sh does not exist")

        data = rrg.load(yaml_path)
        yaml_lines = [
            f"{e['alias']}|{e['provider']}|{e['model_id']}|{e['display']}|{e['tier']}|{e.get('notes', '')}|{e['autocompact_default']}"
            for e in data["entries"]
        ]

        bash = _get_bash()
        cmd = f'source "{sh_agents}"; printf "%s\\n" "${{LA_REMOTE_AGENTS[@]}}"'
        res = subprocess.run([bash, "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        sh_lines = [line for line in res.stdout.strip().splitlines() if line]

        self.assertEqual(yaml_lines, sh_lines)

    def test_validation(self) -> None:
        """Validation rules check schema completeness and constraint violations."""
        if not _has_pyyaml():
            self.skipTest("pyyaml not installed")
        valid_dict = {
            "clear_env_extra": ["OPENAI_API_KEY"],
            "providers": {
                "p1": {
                    "id": "p1",
                    "display": "P1",
                    "tier": "free",
                    "base_url": "https://p1.example.com/v1",
                    "chat_path": "/chat/completions",
                    "models_path": "/models",
                    "catalog_url": "",
                    "key_env": "P1_API_KEY",
                    "extra_env": [],
                    "static_headers": {},
                    "default_models": ["m1"],
                    "supports_responses": False,
                    "notes": "note 1",
                    "litellm_prefix": "openai",
                    "api_base_mode": "static",
                    "generic_openai_base": True,
                },
                "p2": {
                    "id": "p2",
                    "display": "P2",
                    "tier": "free",
                    "base_url": "https://p2.example.com/v1",
                    "chat_path": "/chat/completions",
                    "models_path": "",
                    "catalog_url": "",
                    "key_env": "P2_API_KEY",
                    "extra_env": [],
                    "static_headers": {},
                    "default_models": [],
                    "supports_responses": False,
                    "notes": "",
                    "litellm_prefix": "p2_pref",
                    "api_base_mode": "none",
                    "generic_openai_base": False,
                },
            },
            "entries": [
                {
                    "alias": "alias1",
                    "provider": "p1",
                    "model_id": "m1",
                    "display": "Alias One",
                    "tier": "free",
                    "notes": "no pipes here",
                    "autocompact_default": "-",
                }
            ],
        }

        # Passes validation
        rrg.validate(valid_dict)
        artifacts = rrg.render(valid_dict)
        self.assertIn("_PROVIDER_IDS=", artifacts["bin/remote-provider-registry.sh"])
        parsed_json = json.loads(artifacts["config/remote-registry.json"])
        self.assertEqual(parsed_json["schema_version"], 1)

        # Unknown api_base_mode
        bad_mode = json.loads(json.dumps(valid_dict))
        bad_mode["providers"]["p1"]["api_base_mode"] = "invalid_mode"
        with self.assertRaises(ValueError):
            rrg.validate(bad_mode)

        # Entry with unknown provider
        bad_prov = json.loads(json.dumps(valid_dict))
        bad_prov["entries"][0]["provider"] = "nonexistent"
        with self.assertRaises(ValueError):
            rrg.validate(bad_prov)

        # Duplicate alias
        dup_alias = json.loads(json.dumps(valid_dict))
        dup_alias["entries"].append(dict(dup_alias["entries"][0]))
        with self.assertRaises(ValueError):
            rrg.validate(dup_alias)

        # Pipe in entry notes
        pipe_notes = json.loads(json.dumps(valid_dict))
        pipe_notes["entries"][0]["notes"] = "has|pipe"
        with self.assertRaises(ValueError):
            rrg.validate(pipe_notes)

        # Mapping key != id
        bad_key = json.loads(json.dumps(valid_dict))
        bad_key["providers"]["mismatched"] = bad_key["providers"].pop("p1")
        with self.assertRaises(ValueError):
            rrg.validate(bad_key)

    def test_quoting(self) -> None:
        """Quoting handles single quotes properly in generated bash functions."""
        if not _has_pyyaml():
            self.skipTest("pyyaml not installed")
        data = {
            "clear_env_extra": [],
            "providers": {
                "qprov": {
                    "id": "qprov",
                    "display": "Quote Prov",
                    "tier": "free",
                    "base_url": "https://ex.com/it's/$HOME\"`x`/v1",
                    "chat_path": "/chat",
                    "models_path": "/models",
                    "catalog_url": "",
                    "key_env": "Q_KEY",
                    "extra_env": [],
                    "static_headers": {},
                    "default_models": [],
                    "supports_responses": False,
                    "notes": "",
                    "litellm_prefix": "openai",
                    "api_base_mode": "static",
                    "generic_openai_base": True,
                }
            },
            "entries": [
                {
                    "alias": "qalias",
                    "provider": "qprov",
                    "model_id": "qm",
                    "display": "QM",
                    "tier": "free",
                    "notes": "",
                    "autocompact_default": "-",
                }
            ],
        }

        artifacts = rrg.render(data)
        sh_text = artifacts["bin/remote-provider-registry.sh"]

        bash = _get_bash()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sh") as f:
            f.write(sh_text)
            f.flush()

            cmd = f'source "{f.name}"; _openai_base qprov'
            res = subprocess.run([bash, "-c", cmd], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), "https://ex.com/it's/$HOME\"`x`/v1")

            # The route and catalog arms carry the same literal; a quote there breaks the
            # whole file (bash syntax error) or silently changes the URL.
            res = subprocess.run([bash, "-c", f'source "{f.name}"; _litellm_route qprov m/x'],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), "openai/m/x|https://ex.com/it's/$HOME\"`x`/v1")

    def test_catalog_key_uses_first_line_only(self) -> None:
        """remote-keys.sh --env prints one NAME=value line per env var (cloudflare: two).
        Only the FIRST line is the bearer key; the cut must be at a real newline, not at
        the two characters backslash-n (an easy slip when the shell text is generated)."""
        bash = _get_bash()
        sh = ROOT / "bin" / "remote-provider-registry.sh"
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            keys = tdp / "keys"
            keys.write_text("#!/bin/sh\ncat <<'EOF'\nK=tok\\nen\nSECOND=zzz\nEOF\n")
            keys.chmod(0o755)
            curl = tdp / "curl"
            curl.write_text("#!/bin/sh\ncat\n")
            curl.chmod(0o755)
            env = dict(os.environ, PATH=f"{td}:{os.environ['PATH']}", KEYS=str(keys))
            res = subprocess.run([bash, "-c", f'source "{sh}"; _catalog_request groq'],
                                 capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
            # key contains a literal backslash-n (escaped to \\ for curl config); SECOND never leaks
            self.assertEqual(res.stdout, 'header = "Authorization: Bearer tok\\\\nen"\n')

    def test_cli_help_and_no_args(self) -> None:
        """--help displays help and exits 0; no arguments exit 2."""
        res_help = subprocess.run(
            [sys.executable, str(GENERATOR_PATH), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_help.returncode, 0)
        self.assertIn("--write", res_help.stdout)
        self.assertIn("--check", res_help.stdout)
        self.assertIn("pyyaml", res_help.stdout)

        res_no_args = subprocess.run(
            [sys.executable, str(GENERATOR_PATH)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_no_args.returncode, 2)


if __name__ == "__main__":
    unittest.main()
