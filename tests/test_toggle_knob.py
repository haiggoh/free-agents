#!/usr/bin/env python3
"""tests/test_toggle_knob.py — binary toggles render as a sliding knob (nicegui switch parity).

- ON/OFF rows show "<name>: ON  ──●" / "<name>: OFF ○──" (thin track both ways): no ❮ ❯ glyphs, fixed-width value chip
- toggling animates: the knob passes through intermediate columns before resting at the new end
- multi-choice rows keep their ❮ value ❯ glyphs

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_toggle_knob.py
"""
import os
import sys

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


KNOBS = "".join(g for pair in ec.KNOB_SIZES.values() for g in pair)
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


async def _record_toggle(h, pilot, key, secs=0.5):
    """Press `key` and return every DRAWN frame of that row as (plain tail, knob styles),
    recorded from INSIDE render_model. Sampling from outside misses frames: at 33 ms per frame
    the whole animation (~133 ms) finishes while pilot.press is still processing the key
    (measured 2026-11-08: first outside sample 157-175 ms after the press)."""
    app = h.app
    frames = []

    def snap():
        r = _row_rich(h, key)
        f = (_knob(r.plain)[0], tuple(_knob_styles(r)))
        if not frames or frames[-1] != f:
            frames.append(f)

    snap()
    orig = app.render_model

    def rec(*a, **k):
        orig(*a, **k)
        snap()

    app.render_model = rec
    try:
        await pilot.press(key)
        await pilot.pause(secs)
    finally:
        app.render_model = orig
    return frames


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
            for tail, _ in await _record_toggle(h, pilot, "t"):
                pos = next(i for i, c in enumerate(tail) if c in KNOBS)
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


    def test_table_is_well_formed(self):
        """The frame table (user 2026-11-08) is the single source: 1/60 s ticks (Textual's
        MAX_FPS cap, on the 60 and 120 Hz grids), ends on the far end in the NEW state, keeps
        the OLD state in flight, moves only forward, and the landing flash is the #policy cyan
        held a full 33 ms (2 ticks) — half that was not noticeable."""
        self.assertAlmostEqual(ec.KNOB_TICK_S, 1 / 60)
        seq = ec.KNOB_SEQUENCE
        self.assertEqual(seq[-1][0], END, "the table must end at the far end")
        self.assertEqual(seq[-1][2], "new")
        moved = [r[0] for r in seq]
        self.assertEqual(moved, sorted(moved), "rows must never move the knob backwards")
        for m_, size, state, colour, ticks in seq:
            self.assertIn(size, ec.KNOB_SIZES)
            # user 2026-11-08: a 1-tick (16 ms) frame did not show reliably on screen
            self.assertGreaterEqual(ticks, 2, f"every frame must last >= 2 ticks: {(m_, size, ticks)}")
            if m_ < END:
                self.assertEqual(state, "old", "in flight the knob keeps its OLD state")
            if state != "old":
                self.assertEqual(size, "full", "the state changes only once the knob is full size")
        # user 2026-11-08: ONE size switch only (small while moving and arriving, then full)
        sizes = [r[1] for r in seq]
        switches = sum(1 for a, b in zip(sizes, sizes[1:]) if a != b)
        self.assertEqual(switches, 1, f"the knob may change size exactly once: {sizes}")
        first_new = next(i for i, r in enumerate(seq) if r[2] == "new")
        self.assertEqual(seq[first_new][3], ec.SUBHEADLINE_CYAN,
                         f"the state change itself must flash the subheadline cyan: {seq[first_new]}")
        # user 2026-11-09: right before the cyan, an in-between frame FILLED with a dim colour
        self.assertGreater(first_new, 1)
        self.assertEqual(seq[first_new - 1][1:4], ("full", "filled", "dim"),
                         f"a dim filled frame must precede the cyan: {seq}")
        # user 2026-11-09: no full-size OLD frame — the knob arrives small in the old state and
        # grows straight into the dim fill (that frame replaced the full-size hollow one)
        self.assertEqual(seq[first_new - 2][1:3], ("small", "old"),
                         f"the dim fill must come straight after the small old-state arrival: {seq}")
        self.assertNotIn(("full", "old"), [r[1:3] for r in seq],
                         f"no full-size frame in the old state any more: {seq}")
        flashes = [r for r in seq if r[3] in (ec.SUBHEADLINE_CYAN, "cyan")]
        self.assertEqual(flashes[0][3], ec.SUBHEADLINE_CYAN)     # the subheadline cyan
        for r in flashes:
            self.assertGreaterEqual(r[4] * ec.KNOB_TICK_S, 2 / 60 - 1e-9, f"flash too short: {r}")

    def _expected(self, on: bool):
        """Per tick, the (pos, filled, size, colour) the table prescribes, then the rest frame.
        SHOWS is resolved by the picker's own _knob_shows_filled — one definition, not a copy."""
        start = 0 if on else END
        d = 1 if on else -1
        out = []
        for m_, size, state, colour, ticks in ec.KNOB_SEQUENCE:
            out += [(start + d * m_, self.sp._knob_shows_filled(state, on), size, colour)] * ticks
        return out + [((END if on else 0), on, "full", None)]

    def test_sequence_plays_every_table_row(self):
        """Timer replaced by a recorder: each tick advances exactly one tick of the table, in
        both directions, and every table row is shown for exactly its tick count."""
        async def s(app, pilot, h):
            await self._open()(h)
            self.assertIn("#policy { color: " + ec.SUBHEADLINE_CYAN_CSS + ";", app.CSS)   # cyan single source
            intervals = []
            app.set_interval = lambda delay, cb: (intervals.append(delay) or
                                                  type("T", (), {"stop": lambda self: None})())
            for on in (True, False):
                k = ("SeqProbe", "z")
                app._knob_frame(k, not on)               # at rest in the old state
                seen = [app._knob_frame(k, on)]          # value flipped: row 0, tick 1
                while k in app._knob_anim:
                    app._knob_tick()
                    seen.append(app._knob_frame(k, on))
                self.assertEqual(seen, self._expected(on), f"on={on}")
                self.assertTrue(all(abs(x - ec.KNOB_TICK_S) < 1e-12 for x in intervals), intervals)
            # only the knob carries the flash colour, never the track
            t = self.sp._knob_text(END, True, "full", ec.SUBHEADLINE_CYAN)
            flashed = [t.plain[sp.start:sp.end] for sp in t.spans if str(sp.style) == ec.SUBHEADLINE_CYAN]
            self.assertEqual(flashed, [ec.KNOB_ON], t.spans)
        self.run_picker(s, start="local")

    def test_live_frames_follow_the_table(self):
        """Real timers, frames recorded from INSIDE the render: the drawn sequence (consecutive
        duplicates merged) is exactly the table's, so no row is skipped or reordered."""
        async def s(app, pilot, h):
            await self._open()(h)
            for _ in range(2):                           # OFF→ON, then ON→OFF
                on = _knob(_row(h, "t")[1])[1] != END    # the state we are switching TO
                frames = await _record_toggle(h, pilot, "t")
                # compare glyphs AND the knob's effective style (its own colour, else the
                # track's base: dim when hollow) — so a dim filled frame is told from a plain one
                want = []
                for pos, filled, size, colour in self._expected(on):
                    eff = colour or (None if filled else "dim")
                    f = (self.sp._knob_text(pos, filled, size, colour).plain, eff)
                    if not want or want[-1] != f:
                        want.append(f)
                got = []
                for tail, styles in frames[1:]:          # frames[0] = the row before the press
                    eff = styles[-1] if styles else None # the innermost span = the knob's own
                    if not got or got[-1] != (tail, eff):
                        got.append((tail, eff))
                self.assertEqual(got, want, f"drawn frames differ from the table (on={on})")
                # independent of _knob_shows_filled (which builds `want`): in BOTH directions a
                # drawn frame is the FILLED glyph in dim, right before the cyan (user 2026-11-09)
                i_dim = next((i for i, (t, e) in enumerate(got) if ec.KNOB_ON in t and e == "dim"), None)
                self.assertIsNotNone(i_dim, f"no dim filled frame drawn (on={on}): {got}")
                assert i_dim is not None                 # narrows the type for the index below
                self.assertEqual(got[i_dim + 1][1], "bright_cyan", f"the dim fill must lead into the cyan: {got}")
        self.run_picker(s, start="local")


if __name__ == "__main__":
    import unittest
    unittest.main()
