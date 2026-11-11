#!/usr/bin/env python3
"""tests/test_menu_polish_0555.py — 0.55.5 menu-polish round (user 2026-11-07):

- "<provider default>" / "Provider default" read "not set" for temperature and effort on
  every lane (local, remote, lowkey) everywhere the menu shows them
- Thinking rows read plain "ON" (no "(default)"), shrinking the value cell to ON/OFF width
- the Classifier row wraps EVERYTHING after its first colon in the cycle glyphs (the value
  itself contains ": ", so the old last-colon split mangled it)
- cycle glyphs are PLAIN text by default (terminal default colour, no bold) and light blue
  on hover — colour only, never a background/reverse block
- hue values get one extra leading space to balance the trailing "°"
- theme accent rows: only the colour NAME sits between the glyphs — swatch, #hex and the
  border/shell slices render outside them

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_menu_polish_0555.py
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import ROOT, PilotCase  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive


def _action_option(h, key):
    """(option_index_in_list, the option's prompt Text) for the action row with this key."""
    from textual.widgets import OptionList
    ol = h.app.query_one("#actions", OptionList)
    idx = next(i for i, a in enumerate(h.app.action_ids) if a.key == key)
    opt_i = next(i for i in range(ol.option_count)
                 if ol.get_option_at_index(i).id == f"a:{idx}")
    return opt_i, ol.get_option_at_index(opt_i).prompt


def _between(text):
    plain = text.plain if hasattr(text, "plain") else str(text)
    return plain[plain.rindex("❮") + 1:plain.rindex("❯")]


def _glyph_span_style(prompt, glyph):
    """The rich style string of the span covering `glyph` in the option's prompt Text."""
    plain = prompt.plain
    pos = plain.index(glyph)
    for span in prompt.spans:
        if span.start <= pos < span.end:
            return str(span.style)
    return ""


class DisplayStrings(PilotCase):
    def test_temperature_and_effort_read_not_set(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            temp = str(_action_option(h, "o")[1])
            self.assertEqual(_between(temp).strip(), "not set", temp)
            self.assertNotIn("provider default", temp.lower(), temp)
            # the cell is sized to the row's own widest label ('not set' = 7)
            self.assertEqual(len(_between(temp)), 7 + 2, temp)
            # remote lane: effort shows the sentinel as "not set" (default effort is "max",
            # so cycle it once to the provider-default choice first)
            await h.press("s")
            await h.wait_screen("RemoteScreen")
            await h.expand_settings()
            await h.press("e")
            await h.pilot.pause(0.3)
            eff = str(_action_option(h, "e")[1])
            self.assertEqual(_between(eff).strip(), "not set", eff)
            self.assertNotIn("Provider default", eff, eff)
        self.run_picker(s, start="local")

    def test_thinking_reads_plain_on(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            from picker_harness import action_by_key, assert_glyphs_for
            think = str(_action_option(h, "j")[1])
            self.assertIn("ON", think, think)
            self.assertNotIn("(default)", think, think)
            assert_glyphs_for(self, think, action_by_key(h.app, "j"))   # knob, no ❮❯
            # cycling still reaches OFF and back
            from textual.widgets import OptionList
            ol = h.app.query_one("#actions", OptionList)
            idx = next(i for i, a in enumerate(app.action_ids) if a.key == "j")
            ol.highlighted = next(j for j in range(ol.option_count)
                                  if ol.get_option_at_index(j).id == f"a:{idx}")
            await h.press("right")
            after = str(_action_option(h, "j")[1])
            self.assertIn("OFF", after, after)
            assert_glyphs_for(self, after, action_by_key(h.app, "j"))
            await h.press("left")
            back = str(_action_option(h, "j")[1])
            self.assertIn("ON", back, back)
            assert_glyphs_for(self, back, action_by_key(h.app, "j"))
        self.run_picker(s, start="local")


class ClassifierRow(PilotCase):
    def test_glyphs_wrap_everything_after_the_first_colon(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            await h.press("a")            # auto-mode: blind-trust -> classifier (adds the z row)
            await h.pilot.pause(0.3)
            text = str(_action_option(h, "z")[1])
            seg = _between(text)
            # the WHOLE value sits between the glyphs — exactly the model's display string
            # for the active classifier source (whose values contain ": " themselves)
            import session_picker_model as m
            src = app.screen_model.settings.classifier_source
            self.assertEqual(seg.strip(), m.CLASSIFIER_SOURCE_LABELS[src],
                             f"classifier value not fully inside the glyphs: {text!r}")
            self.assertNotIn("Classifier:", seg)   # the head stays outside
        self.run_picker(s, start="local")


class GlyphStyling(PilotCase):
    def test_glyphs_plain_by_default_light_blue_on_hover_no_background(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            from rich.cells import cell_len
            from textual.widgets import OptionList
            ol = h.app.query_one("#actions", OptionList)
            opt_i, prompt = _action_option(h, "e")            # Effort row (⚙️ emoji prefix; w is a knob toggle now)
            oid = ol.get_option_at_index(opt_i).id
            plain = prompt.plain
            # default: NO colour, NO bold — plain text like the rest of the row
            style = _glyph_span_style(prompt, "❮")
            self.assertNotIn("bright_blue", style, f"base style: {style!r}")
            self.assertNotIn("reverse", style, f"base style: {style!r}")
            self.assertNotIn("bold", style, f"base style: {style!r}")
            # hover: light blue, colour only — no reverse block
            col = 1 + cell_len(plain[:plain.rindex("❯")])
            await h.pilot.hover("#actions", offset=(col, opt_i))
            await h.pilot.pause(0.4)
            self.assertEqual(getattr(app, "_arrow_hover", None), (oid, +1))
            prompt2 = _action_option(h, "e")[1]
            style2 = _glyph_span_style(prompt2, "❯")
            self.assertIn("bright_blue", style2, f"hover style: {style2!r}")
            self.assertNotIn("reverse", style2, f"hover style: {style2!r}")
        self.run_picker(s, start="local")


class HueBalance(PilotCase):
    def seed(self):
        import shutil
        for name in ("local-theme.json", "remote-theme.json"):
            shutil.copy(ROOT / "config" / name, self.themes / name)

    def test_hue_value_shifted_one_space_right_to_balance_the_degree(self):
        async def s(app, pilot, h):
            self.seed()
            await h.press("h")
            await h.wait_screen("ThemeConfigScreen")
            text = str(_action_option(h, "y")[1])
            seg = _between(text)
            val = seg.strip()
            self.assertTrue(val.endswith("°"), text)
            # 0.55.6: the theme screen's rows share ONE value cell (align_cycle_values), so
            # the pad derives from the rendered cell, not a per-row width. The ° rule itself
            # is unchanged: ONE extra leading space vs plain centering.
            pad = len(seg) - len(val) - 2
            left = len(seg) - len(seg.lstrip(" "))
            right = len(seg) - len(seg.rstrip(" "))
            self.assertEqual(left, pad // 2 + 2,
                             f"val={val!r} seg={seg!r} (pad={pad})")
            self.assertGreaterEqual(right, 1, f"no space before ❯: {seg!r}")
        self.run_picker(s)


class LaneAccentRow(PilotCase):
    def seed(self):
        import shutil
        for name in ("local-theme.json", "remote-theme.json"):
            shutil.copy(ROOT / "config" / name, self.themes / name)

    def test_only_the_colour_name_sits_between_the_glyphs(self):
        async def s(app, pilot, h):
            self.seed()
            await h.press("h")
            await h.wait_screen("ThemeConfigScreen")
            text = str(_action_option(h, "l")[1])
            seg = _between(text)
            from session_picker_model import THEME_PRESETS
            names = {n for _, n in THEME_PRESETS}
            self.assertIn(seg.strip(), names,
                          f"colour name not alone between the glyphs: {text!r}")
            self.assertNotIn("#", seg, f"hex must render outside the glyphs: {text!r}")
            after = text[text.rindex("❯"):]
            self.assertIn("#", after, f"hex must still be shown after the glyphs: {text!r}")
        self.run_picker(s)


if __name__ == "__main__":
    unittest.main()
