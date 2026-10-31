#!/usr/bin/env python3
"""bin/emoji_constants.py lane_mark(): 🦾 for a local-inference session, 📡 otherwise
(remote free-API proxy or cloud — the mark names the free lane an emit steers toward).

Usage: python3 tests/test_emoji_lane_mark.py [-v]
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import emoji_constants as ec  # noqa: E402

lane_mark = ec.lane_mark


class LaneMarkTests(unittest.TestCase):
    def test_launcher_env_wins_over_the_port_heuristic(self):
        # LA_SESSION_KIND_EMOJI is exported by bin/la-session-identity.sh at launch
        self.assertEqual(
            lane_mark({"LA_SESSION_KIND_EMOJI": "🦾",
                       "ANTHROPIC_BASE_URL": "http://localhost:4143"}), "🦾")
        self.assertEqual(lane_mark({"LA_SESSION_KIND_EMOJI": "📡"}), "📡")

    def test_port_bands_match_the_identity_resolver(self):
        # same bands as bin/la-session-identity.sh: 8000-8010 local, 4141-4151 free-API proxy
        self.assertEqual(lane_mark({"ANTHROPIC_BASE_URL": "http://localhost:8000"}), "🦾")
        self.assertEqual(lane_mark({"ANTHROPIC_BASE_URL": "http://127.0.0.1:8010"}), "🦾")
        self.assertEqual(lane_mark({"ANTHROPIC_BASE_URL": "http://localhost:4141"}), "📡")
        self.assertEqual(lane_mark({"ANTHROPIC_BASE_URL": "http://[::1]:4151"}), "📡")

    def test_cloud_unknown_and_garbage_default_to_the_remote_mark(self):
        self.assertEqual(lane_mark({"ANTHROPIC_BASE_URL": "https://gw.example"}), "📡")
        self.assertEqual(lane_mark({}), "📡")
        self.assertEqual(lane_mark({"ANTHROPIC_BASE_URL": "not a url"}), "📡")
        # a ☁️ launcher emoji is not a free lane: the emit still names a free lane
        self.assertEqual(lane_mark({"LA_SESSION_KIND_EMOJI": "☁️"}), "📡")


if __name__ == "__main__":
    unittest.main()
