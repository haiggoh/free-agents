"""The local-capable budget is tied to the DETECTED hardware (user 2026-11-23).

Measured defect: the estimator's own read took /proc/meminfo FIRST on Linux and asked nvidia-smi
only when meminfo was missing — which never happens on Linux — so a CUDA box was judged by
system RAM instead of GPU VRAM. Now the canonical bin/la-hw-detect.sh decides the accelerator
kind; the minimal read is only a fallback, and asks the GPU first. No real hardware is read:
the detection and the fallback's command runner are replaced.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
_spec = importlib.util.spec_from_file_location("efp_hw", ROOT / "bin" / "estimate-model-footprint.py")
assert _spec is not None and _spec.loader is not None
fp = importlib.util.module_from_spec(_spec)
sys.modules["efp_hw"] = fp
_spec.loader.exec_module(fp)
_FP: "object" = fp

MEMINFO = "MemTotal:       131072000 kB\nMemFree:        1000 kB\n"


class DeviceBudgetTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, fp, "_DEVICE_BUDGET", None)
        self.addCleanup(setattr, fp, "_canonical_hw", fp._canonical_hw)
        setattr(_FP, "_DEVICE_BUDGET", None)

    def _with_hw(self, hw):
        setattr(_FP, "_canonical_hw", lambda: hw)
        setattr(_FP, "_DEVICE_BUDGET", None)
        return fp._device_memory_gb()

    def test_cuda_box_is_judged_by_vram_not_system_ram(self):
        self.assertEqual(self._with_hw({"LA_HARDWARE": "cuda", "LA_VRAM_GB": "24", "LA_SYSTEM_RAM_GB": "128"}),
                         (24.0, "vram"))
        self.assertEqual(fp.derive_budget_gb(24.0, "vram"), 21.6)

    def test_mac_is_judged_by_unified_ram(self):
        self.assertEqual(self._with_hw({"LA_HARDWARE": "mlx", "LA_VRAM_GB": "", "LA_SYSTEM_RAM_GB": "128"}),
                         (128.0, "unified"))

    def test_rocm_uses_vram_and_cpu_only_uses_ram(self):
        self.assertEqual(fp.budget_from_hardware({"LA_HARDWARE": "rocm", "LA_VRAM_GB": "48"}), (48.0, "vram"))
        self.assertEqual(fp.budget_from_hardware({"LA_HARDWARE": "cpu-only", "LA_SYSTEM_RAM_GB": "64"}),
                         (64.0, "unified"))

    def test_unusable_detection_falls_back(self):
        self.assertEqual(fp.budget_from_hardware({}), (None, None))
        self.assertEqual(fp.budget_from_hardware({"LA_HARDWARE": "cuda", "LA_VRAM_GB": "0"}), (None, None))

    def test_fallback_on_linux_asks_the_gpu_before_meminfo(self):
        calls = []

        def run(argv):
            calls.append(argv[0])
            return "24576\n" if argv[0] == "nvidia-smi" else ""
        self.assertEqual(fp._fallback_device_read("linux", run, MEMINFO), (24.0, "vram"))
        self.assertEqual(calls[0], "nvidia-smi")

    def test_fallback_on_linux_without_gpu_uses_meminfo(self):
        self.assertEqual(fp._fallback_device_read("linux", lambda argv: "", MEMINFO), (125.0, "unified"))

    def test_fallback_on_mac_reads_hw_memsize(self):
        self.assertEqual(fp._fallback_device_read("darwin", lambda argv: str(128 * 2 ** 30), ""),
                         (128.0, "unified"))

    def test_exported_detection_is_used_without_a_subprocess(self):
        import os
        saved = {k: os.environ.get(k) for k in ("LA_HARDWARE", "LA_VRAM_GB", "LA_SYSTEM_RAM_GB")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])
        os.environ.update({"LA_HARDWARE": "cuda", "LA_VRAM_GB": "80", "LA_SYSTEM_RAM_GB": "256"})
        hw = fp._canonical_hw()
        self.assertEqual(hw["LA_HARDWARE"], "cuda")
        self.assertEqual(fp.budget_from_hardware(hw), (80.0, "vram"))


if __name__ == "__main__":
    unittest.main()
