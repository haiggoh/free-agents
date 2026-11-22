#!/usr/bin/env python3
"""Task 6 Step 1 — load-stretched waits (C3): stretched_wait() grows with queue depth and
active cooldown, capped by the patient tier; load_detected() is provider-generic (was
nvidia-only). Main suite; no LiteLLM needed.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
import rate_limiter as rl  # noqa: E402
_spec = importlib.util.spec_from_file_location("provider_limits", REPO / "bin" / "provider_limits.py")
pl = importlib.util.module_from_spec(_spec)
sys.modules["provider_limits"] = pl
_spec.loader.exec_module(pl)

LIMITS_DOC = {
    "schema_version": 1, "session_min_context": 262145, "large_context_threshold": 262144,
    "providers": {
        "nvidia": {"rpm": 40, "tpm": None, "rpd": None, "rpw": None, "tpd": None,
                   "dispatch_only": False, "reserves_on_request": False,
                   "measured_at": "2026-11-20", "source": "test"},
        "mistral": {"rpm": 2, "tpm": 40000, "rpd": None, "rpw": None, "tpd": None,
                    "dispatch_only": False, "reserves_on_request": False,
                    "measured_at": "2026-11-20", "source": "test"},
    },
    "models": {},
}


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class StretchedWaitTests(unittest.TestCase):
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

    def _mistral(self):
        return rl.get_limiter(provider="mistral", clock=self.clock)

    def test_base_with_no_queue(self):
        self.assertEqual(rl.stretched_wait("mistral", 300, clock=self.clock), 300.0)

    def test_queue_depth_stretches(self):
        b = self._mistral()
        # rpm 2: fill the window so two waiters queue
        b.try_acquire(); b.try_acquire()
        b.try_acquire(token="w1"); b.try_acquire(token="w2")   # both queue
        self.assertEqual(rl.stretched_wait("mistral", 300, clock=self.clock), 600.0,
                         "40-equivalent: base * (1 + waiters/rpm)")

    def test_active_cooldown_adds(self):
        b = self._mistral()
        b.note_429()                                            # machine-wide pause 30 s
        w = rl.stretched_wait("mistral", 300, clock=self.clock)
        self.assertGreaterEqual(w, 330.0, f"cooldown must add: {w}")

    def test_cap_honoured(self):
        b = self._mistral()
        b.note_429()
        b.try_acquire(); b.try_acquire()
        b.try_acquire(token="w1"); b.try_acquire(token="w2")
        self.assertEqual(rl.stretched_wait("mistral", 100000, cap=1800.0, clock=self.clock),
                         1800.0)

    def test_unknown_provider_gets_plain_base(self):
        self.assertEqual(rl.stretched_wait("nope", 300, clock=self.clock), 300.0)


class LoadDetectedTests(unittest.TestCase):
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

    def test_quiet_bucket_is_not_loaded(self):
        self.assertFalse(rl.load_detected("mistral", clock=self.clock))

    def test_waiters_at_half_rpm_is_loaded(self):
        b = rl.get_limiter(provider="mistral", clock=self.clock)
        b.try_acquire(); b.try_acquire()                        # window full (rpm 2)
        b.try_acquire(token="w1")                               # 1 waiter >= rpm/2
        self.assertTrue(rl.load_detected("mistral", clock=self.clock))

    def test_cooldown_is_loaded(self):
        b = rl.get_limiter(provider="mistral", clock=self.clock)
        b.note_429()
        self.assertTrue(rl.load_detected("mistral", clock=self.clock))

    def test_missing_state_file_is_not_loaded_and_not_created(self):
        p = self.limiter_dir / "mistral.state"
        self.assertFalse(p.exists())
        self.assertFalse(rl.load_detected("mistral", clock=self.clock))
        self.assertFalse(p.exists(), "load_detected must never create state")

    def test_dispatch_tool_picks_patient_for_any_bucketed_provider(self):
        # the tier call site keys off the bucketed-provider set + load_detected, not nvidia
        spec = importlib.util.spec_from_file_location("free_agent_tool", REPO / "bin" / "free-agent-tool.py")
        fat = importlib.util.module_from_spec(spec)
        sys.modules["free_agent_tool"] = fat
        spec.loader.exec_module(fat)
        self.assertIn("mistral", fat.bucketed_providers())
        b = rl.get_limiter(provider="mistral", clock=self.clock)
        b.try_acquire(); b.try_acquire()
        b.try_acquire(token="w1")
        self.assertTrue(fat.load_detected("mistral", os.environ))
        self.assertFalse(fat.load_detected("gemini", os.environ), "no limits dims -> never loaded")


if __name__ == "__main__":
    unittest.main()
