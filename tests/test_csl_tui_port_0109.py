#!/usr/bin/env python3
"""tests/test_csl_tui_port_0109.py — csl TUI port of the 2026-11-09 GUI rulings
(isolated branch feat/csl-tui-quit-icon-apikeys-order):
1. the Quit action entry carries the ⏻ icon and swaps to ⏼ on hover;
2. in the remote session picker, API keys setup comes BEFORE filter settings
   (so filter settings lands next to the NVIDIA rate limiter).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import emoji_constants as ec  # noqa: E402
import session_picker_model as m  # noqa: E402


def remote_screen():
    s = m.Settings()
    s.last_launched_model = {"local_session": None, "remote_api_session": None, "lowkey": None}
    s.hw_hardware = "mlx"
    return m.RemoteScreen(s, owner=m.DIRECT_ROOT)


class RemoteOrderTests(unittest.TestCase):
    def test_api_keys_before_filter_settings(self):
        acts = [a for a in remote_screen().actions() if a.section != "hidden"]
        keys = {a.key: i for i, a in enumerate(acts)}
        self.assertIn("k", keys, "API keys action exists")
        self.assertIn("y", keys, "filter settings action exists")
        self.assertLess(keys["k"], keys["y"],
                        "API keys (k) must come before filter settings (y)")
        # and the rate limiter sits with them, after API keys
        self.assertIn("n", keys)
        self.assertLess(keys["k"], keys["n"])


class QuitIconTests(unittest.TestCase):
    def test_quit_action_carries_power_icon(self):
        s = m.Settings()
        home = m.HomeScreen(s, owner=m.DIRECT_ROOT)   # DIRECT_ROOT -> the Quit branch of nav_actions
        quit = [a for a in home.nav_actions() if a.key == "q"]
        self.assertEqual(len(quit), 1)
        self.assertIn(ec.EMOJI_QUIT_STR, str(quit[0].label),
                      "quit entry must show the ⏻ icon at rest")

    def test_quit_label_swaps_on_hover(self):
        self.assertIn(ec.EMOJI_QUIT_STR, ec.quit_label(hovered=False))
        self.assertIn("⏼", ec.quit_label(hovered=True))
        self.assertNotIn(ec.EMOJI_QUIT_STR, ec.quit_label(hovered=True))


class QuitIconConfigTests(unittest.TestCase):
    def test_quit_glyphs_are_sourced_from_emoji_sh(self):
        # QC 2026-11-10 (out-of-family review): deleting the EMOJI_QUIT* lines from
        # config/emoji.sh must turn THIS test red — otherwise the code-literal fallback
        # in emoji_constants keeps the suite green while the config is ignored.
        self.assertIsNotNone(ec._EMOJIS.get("EMOJI_QUIT"),
                             "config/emoji.sh must define EMOJI_QUIT")
        self.assertIsNotNone(ec._EMOJIS.get("EMOJI_QUIT_HOVER"),
                             "config/emoji.sh must define EMOJI_QUIT_HOVER")
        self.assertEqual(ec._EMOJIS["EMOJI_QUIT"], ec.EMOJI_QUIT_STR)
        self.assertEqual(ec._EMOJIS["EMOJI_QUIT_HOVER"], ec.EMOJI_QUIT_HOVER_STR)


if __name__ == "__main__":
    unittest.main()
