#!/usr/bin/env python3
"""tests/test_toggle_knob.py — binary toggles render as a sliding knob (nicegui switch parity).

- ON/OFF rows show "<name>: ON  ──●" / "<name>: OFF ○──" (thin track both ways): no ❮ ❯ glyphs, fixed-width value chip
- toggling animates: the knob passes through intermediate columns before resting at the new end
- multi-choice rows keep their ❮ value ❯ glyphs

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_toggle_knob.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import PilotCase  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive



def _row(h, key):
    from textual.widgets import OptionList
    ol = h.app.query_one("#actions", OptionList)
    idx = next(i for i, a in enumerate(h.app.action_ids) if a.key == key)
    opt_i = next(i for i in range(ol.option_count) if ol.get_option_at_index(i).id == f"a:{idx}")
    return opt_i, str(ol.get_option_at_index(opt_i).prompt)


sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import emoji_constants as ec  # noqa: E402

N = ec.KNOB_CELLS
END = N - 1


KNOBS = ec.KNOB_OFF + ec.KNOB_ON + ec.KNOB_OFF_MOVING + ec.KNOB_ON_MOVING
TRACK = set(KNOBS + ec.KNOB_TRACK_OFF + ec.KNOB_TRACK_ON)


def _knob(text):
    """The KNOB_CELLS-wide knob at the end of the row, and the knob's column inside it."""
    tail = text.rstrip()[-N:]
    assert set(tail) <= TRACK, f"no knob at the row end: {text!r}"
    pos = next(i for i, c in enumerate(tail) if c in KNOBS)
    return tail, pos


def _row_rich(h, key):
    """The row's prompt as rich Text (styles intact)."""
    from textual.widgets import OptionList
    ol = h.app.query_one("#actions", OptionList)
    idx = next(i for i, a in enumerate(h.app.action_ids) if a.key == key)
    return next(ol.get_option_at_index(i).prompt for i in range(ol.option_count)
                if ol.get_option_at_index(i).id == f"a:{idx}")


def _knob_styles(rich):
    """Every span style covering the knob character (the last knob glyph in the row)."""
    plain = rich.plain.rstrip()
    i = max(plain.rfind(c) for c in KNOBS)
    return [str(sp.style) for sp in rich.spans if sp.start <= i < sp.end]


class ToggleKnob(PilotCase):
    def _open(self):
        async def go(h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
        return go

    def test_binary_rows_draw_a_knob_not_glyphs(self):
        async def s(app, pilot, h):
            await self._open()(h)
            for key in ("t", "p", "d", "w"):
                text = _row(h, key)[1]
                self.assertNotIn("❮", text, text)
                self.assertNotIn("❯", text, text)
                tail, pos = _knob(text)
                on = ": ON" in text
                self.assertEqual(pos, END if on else 0, f"row {key} knob at the wrong end: {text!r}")
                self.assertNotIn("━", tail, f"row {key}: the track must stay thin: {text!r}")
            # ENABLED/DISABLED rows: the knob follows the value word, not just "ON"
            mcp = _row(h, "m")[1]
            self.assertEqual(_knob(mcp)[1], END if ": ENABLED" in mcp else 0,
                             f"MCP row knob disagrees with its value: {mcp!r}")
            # a multi-choice row keeps the cycle glyphs
            self.assertIn("❮", _row(h, "e")[1])
        self.run_picker(s, start="local")

    def test_toggle_slides_through_intermediate_frames(self):
        async def s(app, pilot, h):
            await self._open()(h)
            _, before = _row(h, "t")
            start = _knob(before)[1]
            seen = []
            await pilot.press("t")
            end = time.time() + 0.6
            while time.time() < end:
                await pilot.pause(0.01)
                pos = _knob(_row(h, "t")[1])[1]
                if not seen or seen[-1] != pos:
                    seen.append(pos)
            final = _knob(_row(h, "t")[1])[1]
            self.assertEqual(final, END - start, f"knob did not land on the other end: {seen}")
            self.assertTrue(any(p not in (0, END) for p in seen),
                            f"no intermediate frame — it jumped instead of sliding: {seen}")
            self.assertNotEqual(before, _row(h, "t")[1], "value text must flip")
            # the thin-track rule must be checked on an ON row: OFF rows are thin either way,
            # so a check on OFF rows alone passed with a heavy ON track (mutation 2026-11-07)
            on_row = _row(h, "t")[1] if final == END else _row(h, "m")[1]
            self.assertEqual(_knob(on_row)[1], END, f"expected an ON row here: {on_row!r}")
            self.assertNotIn("━", _knob(on_row)[0], f"ON track must stay thin: {on_row!r}")
        self.run_picker(s, start="local")


    def test_knob_keeps_its_state_in_flight(self):
        """User 2026-11-07: the knob keeps its OLD state (hollow+dim from OFF, filled from ON)
        while travelling, drawn small; it becomes the new state only on arrival. (The 25 ms
        flash steps are too short to sample reliably here — test_flash_sequence pins them.)"""
        async def s(app, pilot, h):
            await self._open()(h)
            for _ in range(2):                         # OFF→ON, then ON→OFF
                start_on = _knob(_row(h, "t")[1])[1] == END
                frames = []                            # (plain tail, knob styles)
                await pilot.press("t")
                end = time.time() + 0.6
                while time.time() < end:
                    await pilot.pause(0.005)
                    r = _row_rich(h, "t")
                    f = (_knob(r.plain)[0], tuple(_knob_styles(r)))
                    if not frames or frames[-1] != f:
                        frames.append(f)
                arrive = next(i for i, (t, _) in enumerate(frames)
                              if (ec.KNOB_OFF if start_on else ec.KNOB_ON) in t)
                in_flight = frames[:arrive]
                self.assertTrue(in_flight, f"no in-flight frame: {frames}")
                moving = ec.KNOB_ON_MOVING if start_on else ec.KNOB_OFF_MOVING
                for t, _ in in_flight:
                    self.assertIn(moving, t, f"in flight the knob must keep its old state, small: {frames}")
                rest = frames[-1]
                self.assertFalse(any(c in st for st in rest[1] for c in ec.KNOB_FLASH_STEPS),
                                 f"the flash must end: {frames}")
                self.assertIn(ec.KNOB_OFF if start_on else ec.KNOB_ON, rest[0], frames)
        self.run_picker(s, start="local")

    def test_flash_sequence(self):
        """User 2026-11-07: on arrival the knob alone steps through KNOB_FLASH_STEPS (subheadline
        cyan, then an in-between), each held KNOB_FLASH_STEP_S = half a frame, then rests.
        Timers are recorded instead of run, so every step is observed deterministically."""
        async def s(app, pilot, h):
            await self._open()(h)
            self.assertEqual(ec.KNOB_FLASH_STEP_S, ec.KNOB_FRAME_S / 2)
            self.assertEqual(ec.KNOB_FLASH_STEPS[0], "bright_cyan")   # the #policy subheadline cyan
            self.assertIn("#policy { color: ansi_bright_cyan;", app.CSS)
            timers = []
            app.set_timer = lambda delay, cb: timers.append((delay, cb))
            app.set_interval = lambda delay, cb: type("T", (), {"stop": lambda self: None})()
            k = ("FlashProbe", "z")
            app._knob_frame(k, False)                   # first sight: at rest, OFF
            app._knob_frame(k, True)                    # value flipped: slide starts
            for _ in range(ec.KNOB_CELLS - 1):
                app._knob_tick()                        # walk to the ON end
            seen = [app._knob_frame(k, True)]
            while timers:
                delay, cb = timers.pop(0)
                self.assertEqual(delay, ec.KNOB_FLASH_STEP_S)
                cb()
                seen.append(app._knob_frame(k, True))
            flashes = [f[3] for f in seen]
            self.assertEqual(flashes, list(ec.KNOB_FLASH_STEPS) + [None],
                             f"flash steps then rest, in order: {seen}")
            for pos, filled, moving, _ in seen:
                self.assertEqual((pos, filled, moving), (END, True, False), seen)
            # only the knob carries the flash colour, never the track
            t = self.sp._knob_text(END, True, False, ec.KNOB_FLASH_STEPS[0])
            flashed = [t.plain[sp.start:sp.end] for sp in t.spans if str(sp.style) == ec.KNOB_FLASH_STEPS[0]]
            self.assertEqual(flashed, [ec.KNOB_ON], t.spans)
        self.run_picker(s, start="local")


if __name__ == "__main__":
    import unittest
    unittest.main()
