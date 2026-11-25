#!/usr/bin/env python3
"""Task 5 Step 1 — per-provider buckets (C2): get_limiter(provider=...) and the hook's
provider routing. A provider with no limits entry behaves EXACTLY as today (unthrottled,
"limits unknown"); keyed providers get their own state file + rpm from provider-limits.json.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
import rate_limiter as rl  # noqa: E402
_spec = importlib.util.spec_from_file_location("provider_limits", REPO / "bin" / "provider_limits.py")
pl = importlib.util.module_from_spec(_spec)
sys.modules["provider_limits"] = pl
_spec.loader.exec_module(pl)


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


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


class BucketTests(unittest.TestCase):
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
                    "FREE_AGENTS_CONFIG_DIR": str(Path(self.tmp.name) / "menu"),
                    "LA_NVIDIA_MODE": "sliding_window"}   # isolate from the user's saved mode
        self.patcher = unittest.mock.patch.dict(os.environ, self.env, clear=False)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_unknown_provider_is_unlimited_and_labelled(self):
        # Review Focus 1: a provider with NO limits entry behaves exactly as today
        self.assertIsNone(rl.get_limiter(provider="nope", clock=self.clock),
                          "no limits entry -> no limiter at all")
        head = rl.provider_headroom("nope")
        self.assertEqual(head, 1.0, "unknown provider: headroom 1.0 (unlimited)")

    def test_nvidia_keeps_today_path_and_settings(self):
        b = rl.get_limiter(provider="nvidia", clock=self.clock)
        self.assertEqual(str(b.path), self.env["LA_NVIDIA_THROTTLE_STATE"],
                         "nvidia keeps the LA_NVIDIA_THROTTLE_STATE path exactly")
        rpm_equiv = getattr(b, "limit", None) or round(b.rate * 60)
        self.assertEqual(rpm_equiv, 40, "nvidia keeps rpm 40 in either mode")

    def test_mistral_gets_own_state_file_and_rpm(self):
        b = rl.get_limiter(provider="mistral", clock=self.clock)
        self.assertEqual(str(b.path),
                         str(self.limiter_dir / "mistral.state"),
                         "non-nvidia providers live under FA_LIMITER_DIR/<provider>.state")
        self.assertEqual(b.limit, 2)
        # rpm 2: the third request in 60 s waits ~30 s on the fake clock
        self.assertEqual(b.try_acquire(), 0.0)
        self.assertEqual(b.try_acquire(), 0.0)
        wait = b.try_acquire()
        self.assertGreater(wait, 0.0)
        self.assertLessEqual(wait, 60.0)
        self.assertGreaterEqual(wait, 29.0, f"third request waits ~30s, got {wait}")

    def test_buckets_of_two_providers_are_independent(self):
        n = rl.get_limiter(provider="nvidia", clock=self.clock)
        m = rl.get_limiter(provider="mistral", clock=self.clock)
        for _ in range(40):
            self.assertEqual(n.try_acquire(), 0.0)      # nvidia window fills
        self.assertGreater(n.try_acquire(), 0.0, "nvidia saturated")
        self.assertEqual(m.try_acquire(), 0.0, "mistral unaffected by nvidia's window")

    def test_tpm_books_tokens_in_sidecar(self):
        b = rl.get_limiter(provider="mistral", clock=self.clock)
        self.assertEqual(b.try_acquire(tokens=30000), 0.0)
        # 40000 tpm: 10000 left -> a 30000-token request waits
        wait = b.try_acquire(tokens=30000)
        self.assertGreater(wait, 0.0, "tpm sidecar blocks the over-budget request")
        # and it can NEVER fit if over tpm:
        self.assertEqual(b.try_acquire(tokens=50000), float("inf"), "over tpm: ineligible")

    def test_headroom_reflects_limits(self):
        b = rl.get_limiter(provider="mistral", clock=self.clock)
        self.assertEqual(rl.provider_headroom("mistral", clock=self.clock), 1.0)
        b.try_acquire()
        h = rl.provider_headroom("mistral", clock=self.clock)
        self.assertLess(h, 1.0, "after one grant headroom < 1")
        self.assertGreater(h, 0.0)


class HookRoutingTests(unittest.TestCase):
    """The hook routes each deployment to its provider's bucket."""

    def test_provider_of_maps_prefixes(self):
        spec = importlib.util.spec_from_file_location("la_proxy_hooks", REPO / "bin" / "la_proxy_hooks.py")
        # import without litellm: the module tolerates it (CustomLogger=object)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["la_proxy_hooks"] = mod
        spec.loader.exec_module(mod)
        self.assertEqual(mod._provider_of({"model": "nvidia_nim/x"}), "nvidia")
        self.assertEqual(mod._provider_of({"model": "mistral/codestral"}), "mistral")
        self.assertEqual(mod._provider_of({"model": "groq/qwen"}), "groq")
        self.assertEqual(mod._provider_of({"custom_llm_provider": "nvidia_nim"}), "nvidia")
        self.assertEqual(mod._provider_of({"model": "local/x"}), "local")


if __name__ == "__main__":
    unittest.main()
