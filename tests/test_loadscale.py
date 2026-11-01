#!/usr/bin/env python3
"""tests/test_loadscale.py — the load->timeout mapping used by the PTY/litellm tests.

Usage: python3 tests/test_loadscale.py [-v]
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loadscale  # noqa: E402


class LoadScaleTests(unittest.TestCase):
    def test_mapping_clamps(self):
        self.assertEqual(loadscale.scale_for(0), 1.0)
        self.assertEqual(loadscale.scale_for(1), 1.0)
        self.assertEqual(loadscale.scale_for(2), 1.0)     # idle-machine numbers stay x1
        self.assertEqual(loadscale.scale_for(8), 4.0)
        self.assertEqual(loadscale.scale_for(16), 8.0)
        self.assertEqual(loadscale.scale_for(100), 8.0)   # clamped

    def test_load_avg1_is_a_float(self):
        la = loadscale.load_avg1()
        self.assertIsInstance(la, float)
        self.assertGreaterEqual(la, 0.0)

    def test_current_scale_sampled_per_call_and_in_range(self):
        s = loadscale.current_scale()
        self.assertGreaterEqual(s, 1.0)
        self.assertLessEqual(s, 8.0)
        self.assertEqual(s, loadscale.scale_for(loadscale.load_avg1()))


if __name__ == "__main__":
    unittest.main()
