#!/usr/bin/env python3
"""theme_colors.py — derived theme colours shared by both settings generators.

shell_mode_color(): the BASH-BORDER colour for the input field. Stock Claude Code paints
shell mode (`!` at the prompt) MAGENTA; the themes used to override it with the accent, which
made shell mode visually indistinguishable from normal input (user 2026-10-23). The colour is
DERIVED from the theme's main colour — hue +20°, saturation 100%, value 100% (the user's
recipe) — so it is clearly distinct at a glance and never a second hand-kept value.

Usage (library; generate-local-settings.py / generate-remote-settings.py import it):
    shell_mode_color(accent_hex) -> "#RRGGBB"
"""
import colorsys


def shell_mode_color(accent_hex: str) -> str:
    """The theme's main colour shifted +20° in hue, saturation and value at 100%."""
    hex_color = (accent_hex or "").strip()
    if not (hex_color.startswith("#") and len(hex_color) >= 7):
        hex_color = "#00BFFF"            # the generators' own fallback accent
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except ValueError:
        r, g, b = 0.0, 0.75, 1.0
    h, _s, _v = colorsys.rgb_to_hsv(r, g, b)
    r2, g2, b2 = colorsys.hsv_to_rgb(((h * 360 + 20) % 360) / 360, 1.0, 1.0)
    return "#{:02X}{:02X}{:02X}".format(*(round(c * 255) for c in (r2, g2, b2)))


def main(argv=None) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print((__doc__ or "").strip())
        return 0
    for a in (argv or []):
        print(shell_mode_color(a))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
