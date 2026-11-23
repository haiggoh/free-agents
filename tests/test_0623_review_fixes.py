"""0.62.3 — fixes for the 0.62.0 whole-branch review findings (waypoint 0-62-0-whole-branch-review).

F1  provider_headroom must compare the RIGHT units per dimension: requests against
    rpd/rpw, tokens against tpm/tpd. 0.62.0 compared day/week TOKENS against the rpd/rpw
    REQUEST limits and read a tpm "tokens" key the snapshot never has (-> 0), so tpd was
    unused and day/week headroom was garbage (e.g. 0.0 when it should be 0.5).
F2  smooth_bucket status() must report in_window + grants like the sliding window does;
    0.62.0 omitted them, so the rpm headroom term was always 1.0 in the live smooth mode.
F4  dispatch_entry must apply the RESOLVED effort level to the wire (--reasoning-effort);
    0.62.0 recorded rec["effort"] in the receipt but never sent it.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import rate_limiter as rl  # noqa: E402
import importlib.util  # noqa: E402

_TOOL = ROOT / "bin" / "free-agent-tool.py"
_spec = importlib.util.spec_from_file_location("free_agent_tool_0623", _TOOL)
fat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fat)


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


LIMITS_DOC = {
    "schema_version": 1, "session_min_context": 262145, "large_context_threshold": 262144,
    "providers": {
        "acme": {"rpm": 40, "tpm": 1000, "rpd": 10, "rpw": 100, "tpd": 1000,
                 "dispatch_only": False, "reserves_on_request": False,
                 "measured_at": "2026-11-23", "source": "test"},
        "nvidia": {"rpm": 40, "tpm": None, "rpd": None, "rpw": None, "tpd": None,
                   "dispatch_only": False, "reserves_on_request": False,
                   "measured_at": "2026-11-23", "source": "test"},
    },
    "models": {},
}


class HeadroomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.limits_path = Path(self.tmp.name) / "provider-limits.json"
        self.limits_path.write_text(json.dumps(LIMITS_DOC))
        self.limiter_dir = Path(self.tmp.name) / "limiter"
        self.env = {"FA_PROVIDER_LIMITS": str(self.limits_path),
                    "FA_LIMITER_DIR": str(self.limiter_dir),
                    "LA_NVIDIA_THROTTLE_STATE": str(Path(self.tmp.name) / "nvidia-state"),
                    "LA_SESSION_MENU_CONFIG_DIR": str(Path(self.tmp.name) / "menu"),
                    "LA_NVIDIA_MODE": "sliding_window"}
        self.patcher = unittest.mock.patch.dict(os.environ, self.env, clear=False)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_f1_headroom_uses_requests_vs_rpd_and_tokens_vs_tpd(self):
        b = rl.get_limiter(provider="acme", clock=self.clock)
        self.assertIsNotNone(b)
        # 5 requests, 100 tokens each: day requests=5/10, day tokens=500/1000 tpd,
        # tpm window used=500/1000, rpm window 5/40, week requests 5/100.
        for _ in range(5):
            self.assertEqual(b.try_acquire(tokens=100), 0.0)
        h = rl.provider_headroom("acme", clock=self.clock)
        # min(rpm 0.875, tpm 0.5, rpd 0.5, tpd 0.5, rpw 0.95) = 0.5
        # 0.62.0 code: day TOKENS 500 vs rpd 10 -> 1-50 -> clamped 0.0  (WRONG)
        self.assertAlmostEqual(h, 0.5, places=6,
                               msg=f"headroom must be 0.5 (tpm/rpd/tpd all 0.5), got {h}")

    def test_f1_tpm_window_counts_tokens_not_requests(self):
        b = rl.get_limiter(provider="acme", clock=self.clock)
        self.assertIsNotNone(b)
        b.try_acquire(tokens=900)          # one huge request: tpm 900/1000 used
        h = rl.provider_headroom("acme", clock=self.clock)
        self.assertAlmostEqual(h, 0.1, places=6,
                               msg=f"tpm headroom should be 0.1 (900/1000 tokens), got {h}")

    def test_f2_smooth_bucket_status_reports_window_and_grants(self):
        with unittest.mock.patch.dict(os.environ, {"LA_NVIDIA_MODE": "smooth_bucket"}):
            b = rl.get_limiter(provider="nvidia", clock=self.clock)
            self.assertIsNotNone(b)
            self.assertEqual(b.try_acquire(), 0.0)
            self.assertEqual(b.try_acquire(), 0.0)
            st = b.status()
            self.assertEqual(st.get("in_window"), 2,
                             f"smooth status must report in_window=2, got {st}")
            self.assertIn("grants", st, "smooth status must carry the grants list")
            h = rl.provider_headroom("nvidia", clock=self.clock)
            self.assertAlmostEqual(h, 1 - 2 / 40, places=6,
                                   msg=f"rpm headroom in smooth mode must be 38/40, got {h}")


class EffortWireTests(unittest.TestCase):
    """F4: the resolved effort level reaches the remote dispatch's wire flag."""

    def _run_dispatch(self, effort):
        entry = {"kind": "remote", "provider": "nvidia", "model": "acme/x-model",
                 "alias": "acme-x"}
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            outdir = cmd[cmd.index("--outdir") + 1]
            Path(outdir, "output.txt").write_text("ok")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with unittest.mock.patch.object(fat.subprocess, "run", fake_run):
            fat.dispatch_entry(entry, "hi", {}, effort=effort)
        return captured["cmd"]

    def test_f4_resolved_effort_maps_to_reasoning_effort(self):
        import effort_resolve
        for level, wire in (("low", "low"), ("medium", "medium"), ("high", "high"),
                            ("xhigh", "high"), ("max", "high")):
            self.assertEqual(effort_resolve.effort_to_reasoning(level), wire)
            cmd = self._run_dispatch(level)
            i = cmd.index("--reasoning-effort")
            self.assertEqual(cmd[i + 1], wire,
                             f"effort={level} must wire --reasoning-effort {wire}, got {cmd[i+1:]}")

    def test_f4_no_effort_keeps_entry_reasoning_effort(self):
        entry = {"kind": "remote", "provider": "nvidia", "model": "acme/x",
                 "alias": "acme-x", "reasoning_effort": "medium"}
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            Path(cmd[cmd.index("--outdir") + 1], "output.txt").write_text("ok")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with unittest.mock.patch.object(fat.subprocess, "run", fake_run):
            fat.dispatch_entry(entry, "hi", {}, effort=None)
        cmd = captured["cmd"]
        self.assertIn("--reasoning-effort", cmd)
        self.assertEqual(cmd[cmd.index("--reasoning-effort") + 1], "medium")


if __name__ == "__main__":
    unittest.main()
