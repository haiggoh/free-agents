#!/usr/bin/env python3
"""tests/test_cuda_managers.py — the four registered CUDA managers (plan Task 14).

No CUDA hardware here: every test stubs the command runner and pip. Covers: side-by-side
versioned venvs, the GPU smoke GATING promote (a failed smoke leaves the live pin untouched),
dead venv -> broken, the legacy unversioned dir listed as "legacy", --cuda-version validation,
the PyTorch index mapping, and sglang's --only-binary flashinfer-python (never source-build).

Run: ~/.local/pipx/venvs/litellm/bin/python -m pytest tests/test_cuda_managers.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "install"))

import managers  # noqa: E402
import managers._cuda  # noqa: F401,E402
import managers.litellm  # noqa: F401,E402
import managers.llama_cpp  # noqa: F401,E402
import managers.moespresso  # noqa: F401,E402
import managers.omlx  # noqa: F401,E402
import managers.rapid_mlx  # noqa: F401,E402
import managers.vllm_mlx  # noqa: F401,E402
from managers._common import ManagerError  # noqa: E402


def make_venv(root: Path, prefix: str, version: str, binary: str) -> Path:
    target = root / f"{prefix}-{version}"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / binary
    script.write_text(f"#!{target}/bin/python\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    script.chmod(0o755)
    return target


def break_venv(target: Path) -> None:
    dead = target / "bin" / "python-dead-keg"
    for name in os.listdir(target / "bin"):
        link = target / "bin" / name
        if name.startswith("python") and link.is_symlink():
            link.unlink()
            link.symlink_to(dead)


class FakeResult:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


class FakeRunner:
    """Records commands; per-command scripted results via a handler."""
    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or (lambda cmd: FakeResult(0, "ok"))

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        return self.handler(cmd)


class CudaRegistryTest(unittest.TestCase):
    def test_ten_managers_registered_including_four_cuda(self):
        names = managers.list_managers()
        self.assertEqual(len(names), 10, names)
        for n in ("cuda-vllm", "cuda-llama-cpp", "cuda-exllamav2", "cuda-sglang"):
            self.assertIn(n, names)


class CudaManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.state = base / "state"
        self.live = base / "live"
        self.home.mkdir()
        self.live.mkdir()
        (self.live / "config.local.sh").write_text(
            '# live\nLA_CUDA_BIN="$HOME/.local/share/free-agents/cuda-backends/cuda-vllm-0.8.0/bin/vllm"\n',
            encoding="utf-8")
        os.environ["LA_USER_STATE_DIR"] = str(self.state)
        os.environ["LA_LIVE_CONFIG_DIR"] = str(self.live)
        self.addCleanup(os.environ.pop, "LA_USER_STATE_DIR", None)
        self.addCleanup(os.environ.pop, "LA_LIVE_CONFIG_DIR", None)
        self.root = self.state / "cuda-backends"
        make_venv(self.root, "cuda-vllm", "0.8.0", "vllm")
        self.mgr = managers.get_manager_instance("cuda-vllm", self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_build(self, target: Path, version: str, index_url: str, runner) -> None:
        (target / "bin").mkdir(parents=True)
        script = target / "bin" / "vllm"
        script.write_text(f"#!{target}/bin/python\nimport sys\nsys.exit(0)\n")
        script.chmod(0o755)
        (target / "bin" / "python").symlink_to(Path(sys.executable))

    def test_install_builds_side_by_side_then_promote_writes_live_pin(self):
        self.mgr._build = self._fake_build
        self.mgr.gpu_smoke = lambda *a, **k: None
        self.mgr.validate_environment = lambda v: {"version": v}
        runner = FakeRunner()
        info = self.mgr.install_version("0.9.0", cuda_version="12.8", runner=runner)
        self.assertEqual(info.status, "complete")
        self.assertTrue((self.root / "cuda-vllm-0.9.0" / "bin" / "vllm").exists())
        self.assertTrue((self.root / "cuda-vllm-0.8.0").is_dir())  # side-by-side, not rmtree'd
        self.mgr.promote_pins("0.9.0")
        live_text = (self.live / "config.local.sh").read_text()
        self.assertIn("cuda-vllm-0.9.0", live_text)

    def test_failing_gpu_smoke_blocks_promote_and_leaves_pin_untouched(self):
        self.mgr._build = self._fake_build
        smoke = {"ok": True}
        def maybe_bad_smoke(target, runner=None):
            if not smoke["ok"]:
                raise ManagerError("GPU smoke failed: no CUDA device visible")
        self.mgr.gpu_smoke = maybe_bad_smoke
        runner = FakeRunner()
        self.mgr.install_version("0.9.0", cuda_version="12.8", runner=runner)  # smoke ok during install
        smoke["ok"] = False                                                        # ...but fails at promote
        with self.assertRaises(ManagerError):
            self.mgr.promote_pins("0.9.0")
        live_text = (self.live / "config.local.sh").read_text()
        self.assertIn("cuda-vllm-0.8.0", live_text)
        self.assertNotIn("0.9.0", live_text)

    def test_dead_venv_is_broken_not_complete(self):
        break_venv(self.root / "cuda-vllm-0.8.0")
        rows = dict((v, s) for v, _p, s in self.mgr.installed_versions())
        self.assertEqual(rows.get("0.8.0"), "broken")

    def test_legacy_unversioned_dir_is_listed_as_legacy(self):
        legacy = self.root / "cuda-vllm"
        (legacy / "bin").mkdir(parents=True)
        rows = dict((v, s) for v, _p, s in self.mgr.installed_versions())
        self.assertIn("legacy", rows)

    def test_cuda_version_override_malformed_aborts_naming_it(self):
        with self.assertRaises(ManagerError) as ctx:
            self.mgr.detect_cuda("12", runner=FakeRunner())
        self.assertIn("--cuda-version", str(ctx.exception))

    def test_cuda_override_and_nvcc_detection(self):
        self.assertEqual(self.mgr.detect_cuda("12.8", runner=FakeRunner()), "12.8")
        runner = FakeRunner(lambda cmd: FakeResult(0, "Cuda compilation tools, release 12.6, V12.6.85"))
        self.assertEqual(self.mgr.detect_cuda(None, runner=runner), "12.6")

    def test_pytorch_index_maps_and_falls_back(self):
        self.assertTrue(self.mgr.pytorch_index("12.8").endswith("/cu128"))
        self.assertTrue(self.mgr.pytorch_index("12.3").endswith("/cu121"))  # nearest lower 12.x
        self.assertTrue(self.mgr.pytorch_index("99.9").endswith("/cu121"))  # unknown -> safe default

    def test_sglang_refuses_source_build_of_flashinfer(self):
        sgl = managers.get_manager_instance("cuda-sglang", self.home)
        cmds = []
        def handler(cmd):
            cmds.append(list(cmd))
            return FakeResult(1, "", "Could not find a version that satisfies flashinfer-python")
        with self.assertRaises(ManagerError) as ctx:
            sgl._build(self.root / "cuda-sglang-1.0.0", "1.0.0", "https://download.pytorch.org/whl/cu128",
                       FakeRunner(handler))
        flat = " ".join(" ".join(c) for c in cmds)
        self.assertIn("--only-binary flashinfer-python", flat.replace("--only-binary", "--only-binary"))
        self.assertIn("flashinfer", str(ctx.exception).lower())
        self.assertFalse((self.root / "cuda-sglang-1.0.0").exists())  # cleaned up


if __name__ == "__main__":
    unittest.main()
