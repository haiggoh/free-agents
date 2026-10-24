#!/usr/bin/env python3
"""Shell-mode input color (0.43.1): the theme set `bashBorder` to the SAME accent as every
other surface, so Claude Code's stock MAGENTA shell-mode signal was overwritten and `!` mode
was visually indistinguishable from normal input (user 2026-10-23).

Contract: bashBorder is DERIVED from the theme's main color — hue +20°, saturation 100%,
value 100% (the user's recipe) — so it is clearly distinct at a glance, and derived (never a
second hand-kept value in the theme files)."""
import colorsys
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_generator(name):
    if str(ROOT / "bin") not in sys.path:
        sys.path.insert(0, str(ROOT / "bin"))   # sibling imports (theme_colors) resolve as in production
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def hsv_of(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360, s, v


LOCAL = load_generator("generate-local-settings.py")
REMOTE = load_generator("generate-remote-settings.py")
ACCENT = "#FFD700"                      # the shipped local theme's main color


class ShellModeColor(unittest.TestCase):
    def setUp(self):
        # LA_THEME_CONFIG_DIR: the documented test escape hatch — without it the generators
        # read the USER's real theme store (the yellow one) and the fixture is ignored.
        self.themedir = Path(tempfile.mkdtemp())
        self.old_env = os.environ.get("LA_THEME_CONFIG_DIR")
        os.environ["LA_THEME_CONFIG_DIR"] = str(self.themedir)
        self.addCleanup(lambda: (os.environ.pop("LA_THEME_CONFIG_DIR", None) if self.old_env is None
                                 else os.environ.__setitem__("LA_THEME_CONFIG_DIR", self.old_env)))

    def _write_theme(self, name, theme):
        (self.themedir / name).write_text(json.dumps(theme))

    def build(self, gen, theme_overrides=None):
        self._write_theme("local-theme.json",
                          {"theme": {"accent_color": ACCENT, "identity_label": "Test session",
                                     **(theme_overrides or {})}})
        return gen.build_theme_file(Path(tempfile.mkdtemp())), self.themedir

    def test_bash_border_is_not_the_accent_or_the_prompt_border(self):
        for gen in (LOCAL, REMOTE):
            with self.subTest(gen.__name__):
                bundle, _ = self.build(gen)
                ov = bundle["overrides"]
                self.assertNotEqual(ov["bashBorder"], ACCENT,
                                    "shell mode must be visually distinct from normal input")
                self.assertNotEqual(ov["bashBorder"], ov["promptBorder"])

    def test_bash_border_follows_the_recipe_hue_plus_20_sat_value_100(self):
        bundle, _ = self.build(LOCAL)
        h, s, v = hsv_of(bundle["overrides"]["bashBorder"])
        accent_h, _, _ = hsv_of(ACCENT)
        self.assertAlmostEqual(h, (accent_h + 20) % 360, delta=1.5)
        self.assertAlmostEqual(s, 1.0, places=2)
        self.assertAlmostEqual(v, 1.0, places=2)

    def test_bash_border_follows_the_accent_when_the_theme_changes(self):
        # Derived, never a second hand-kept value: change the main color, the shell color moves.
        blue = "#00BFFF"
        self._write_theme("local-theme.json",
                          {"theme": {"accent_color": blue, "identity_label": "Test"}})
        ov = LOCAL.build_theme_file(Path(tempfile.mkdtemp()))["overrides"]
        h, s, v = hsv_of(ov["bashBorder"])
        accent_h, _, _ = hsv_of(blue)
        self.assertAlmostEqual(h, (accent_h + 20) % 360, delta=1.5)
        self.assertNotEqual(ov["bashBorder"], blue)

    def test_remote_generator_gets_the_same_contract(self):
        self._write_theme("remote-theme.json",
                          {"theme": {"accent_color": ACCENT, "identity_label": "Test"}})
        ov = REMOTE.build_theme_file(Path(tempfile.mkdtemp()))["overrides"]
        self.assertNotEqual(ov["bashBorder"], ACCENT)
        h, s, v = hsv_of(ov["bashBorder"])
        self.assertAlmostEqual(s, 1.0, places=2)
        self.assertAlmostEqual(v, 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
