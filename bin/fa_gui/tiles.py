"""fa_gui/tiles.py — the modern-mode tile size policy (user rulings 2026-11-08/09).

Pure function over (screen name, RenderedControl, Action) so it is unit-testable without
a browser. Returns "tile-lg" (2x2 square), "tile-wide" (2x1), "" (1x1), or None (NOT a
tile — rendered as a text row after the grid).

Rules (newest user prompt wins):
- Go launch = 2x2 in every session picker
- home lanes Local/Remote = 2x2; Lowkey, Backend manager, API keys, RAM protection = 2x1
- remote picker: API keys 2x1 and BEFORE filter settings (filter settings then lands on
  the second row next to the rate limiter — grid auto-placement handles the rows)
- cloud config: every option except Back-to-Home = 2x1
- choose-model (section "models") = never a tile, text row after the tiles
"""
from __future__ import annotations

WIDE_TARGETS = {"tool:lowkey", "runtime_manager", "api_keys", "memguard"}
WIDE_LABELS = ("Backend manager", "RAM usage", "out-of-memory", "API keys")


def tile_cls(screen_name: str, c, action) -> str | None:
    if action is not None and getattr(action, "section", "") == "models":
        return None                      # choose-model: text row, not a tile
    label = str(getattr(c, "label", "") or "")
    target = getattr(c, "target", "") or ""
    if "Go launch" in label:
        return "tile-lg"
    if any(w in label for w in WIDE_LABELS):
        return "tile-wide"
    if screen_name == "HomeScreen" and action is not None and action.section == "lanes" \
            and target in ("local", "remote"):
        return "tile-lg"
    if screen_name == "CloudConfigScreen" and target not in ("back", "home", "quit"):
        return "tile-wide"   # button rows have target "" — still wide, only Back/Quit are not
    if target in WIDE_TARGETS:
        return "tile-wide"
    return ""
