#!/usr/bin/env python3
"""tests/test_gui_tiles.py — the modern-mode tile size policy (fa_gui/tiles.py).

User rulings 2026-11-08/09 (newest wins): Go launch 2x2 in every session picker; home lanes
Local/Remote 2x2; Lowkey/Backend manager/API keys/RAM protection 2x1; remote picker API keys
2x1 before filter settings; cloud config all-except-Back 2x1; choose-model never a tile.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin" / "fa_gui"))
import session_picker_model as m  # noqa: E402
import adapter  # noqa: E402
from tiles import tile_cls  # noqa: E402


def ctrl(label, target=""):
    return adapter.RenderedControl(action_id="x", kind="nav", label=label, target=target)


def act(section="tools", key="x"):
    return m.Action(key, "lbl", lambda: None, section=section)


class TilePolicyTests(unittest.TestCase):
    def test_go_launch_is_2x2_everywhere(self):
        for screen in ("LocalScreen", "RemoteScreen", "LowkeyScreen", "HomeScreen"):
            self.assertEqual(tile_cls(screen, ctrl("🚀 Go launch: X session"), act()),
                             "tile-lg", screen)

    def test_home_lanes_big_others_small(self):
        self.assertEqual(tile_cls("HomeScreen", ctrl("🦾 Local sessions", "local"),
                                  act(section="lanes")), "tile-lg")
        self.assertEqual(tile_cls("HomeScreen", ctrl("📡 Remote free API sessions", "remote"),
                                  act(section="lanes")), "tile-lg")
        self.assertEqual(tile_cls("HomeScreen", ctrl("💬 Lowkey", "tool:lowkey"),
                                  act(section="lanes")), "tile-wide")
        self.assertEqual(tile_cls("HomeScreen", ctrl("📥 Download local models", "tool:download"),
                                  act()), "")

    def test_wide_tools_everywhere(self):
        for tgt in ("runtime_manager", "api_keys", "memguard", "tool:lowkey"):
            self.assertEqual(tile_cls("LocalScreen", ctrl("x", tgt), act()), "tile-wide", tgt)
        # button rows with no target match by label
        self.assertEqual(tile_cls("LocalScreen", ctrl("🔧 Backend manager (Rapid-MLX)"),
                                  act()), "tile-wide")
        self.assertEqual(tile_cls("LocalScreen", ctrl("🛡️ RAM usage out-of-memory protection"),
                                  act()), "tile-wide")

    def test_cloud_config_all_wide_except_back(self):
        self.assertEqual(tile_cls("CloudConfigScreen", ctrl("☁️ Cloud session configuration…",
                                                             "cloud_config"), act()), "tile-wide")
        self.assertEqual(tile_cls("CloudConfigScreen", ctrl("🏠 Back to Home", "home"),
                                  act(section="nav")), "")
        self.assertEqual(tile_cls("CloudConfigScreen", ctrl("Quit", "quit"),
                                  act(section="nav")), "")

    def test_choose_model_never_a_tile(self):
        self.assertIsNone(tile_cls("LocalScreen", ctrl("Choose model…"), act(section="models")))
        self.assertIsNone(tile_cls("RemoteScreen", ctrl("Choose model…"), act(section="models")))


if __name__ == "__main__":
    unittest.main()
