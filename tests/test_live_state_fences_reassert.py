#!/usr/bin/env python3
"""Gate flake 2026-11-27 (free-agents 0.64.2 ship gate, xdist worker gw11): HF tests pop
LA_HF_PARAMS_CACHE / LA_HF_API_BASE / LA_HF_ROUTER_URL with a plain os.environ.pop on cleanup,
and nothing re-asserts the import-time fences for the NEXT test in the same worker. The session
ladder (bin/session_ladder.py:61 -> provider_limits._with_runtime_facts "hf" layer) then resolves
a snapshot-unknown context from the REAL warm HF cache instead of the fenced per-worker one, so
test_unknown_context_skips_with_reason picks nvidia-glm53 instead of skipping it.

This file is the red/green proof for the fix in conftest's _reassert_live_state_fences: the
popper mimics the HF tests' cleanup pop; the checker asserts the fences are back for the next
test. RED before the conftest fix, GREEN after.
"""
import os
import unittest

_FENCED_KEYS = ("LA_HF_PARAMS_CACHE", "LA_HF_API_BASE", "LA_HF_ROUTER_URL")


class _Popper(unittest.TestCase):
    def test_00_pop_the_hf_fences_like_hf_test_cleanups_do(self):
        for k in _FENCED_KEYS:
            os.environ.pop(k, None)


class _FenceCheck(unittest.TestCase):
    def test_01_hf_fences_are_reasserted_for_the_next_test(self):
        self.assertEqual(os.environ.get("LA_HF_API_BASE"), "http://127.0.0.1:9")
        self.assertEqual(os.environ.get("LA_HF_ROUTER_URL"), "http://127.0.0.1:9/v1/models")
        cache = os.environ.get("LA_HF_PARAMS_CACHE", "")
        self.assertIn("fa-hfparams-test-", cache,
                      "LA_HF_PARAMS_CACHE must be the fenced per-worker file, not unset/real")


if __name__ == "__main__":
    unittest.main()
