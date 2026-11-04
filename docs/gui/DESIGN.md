# GUI design decision — Task 8 (2026-11-04)

Two static NiceGUI directions were built over synthetic Home + Remote data
(`docs/gui/design/direction_{a,b}.py`, shot by `shoot.py` via Playwright at 1280×800 and
900×700, light and dark — 16 screenshots in `docs/gui/screens/`).

**Chosen: direction A — "Terminal Night"** (dark-first, lime #ADFF2F accent = the remote
lane's theme hue, left-bordered action rows, monospace only for alias ids).

Critique notes (why A over B):
- A matches the existing product identity: the TUI is a terminal launcher (📡 🦾, lane emoji,
  mono aliases), so a dark terminal-native frame is coherent, not a costume. B's cream card
  grid reads as a generic SaaS page and its home cards wrapped ("Backend manager" on two
  lines in a 176px card) — the card kit is exactly the templated look the design skills flag.
- A's remote list maps 1:1 onto the accordion structure (group headers + rows with size tag +
  mono id), so Task 9's adapter needs no layout invention.
- B's size pills (yellow chips) are a nice touch — port them onto A's rows as the size tag
  style when building the real screen.
- Both directions kept: proportional UI font everywhere, mono only for aliases/commands, lane
  emoji intact, version footer dim/italic. Dark and light variants of A both render correctly
  (light variant = #f6f8fa ground); B's dark variant also worked but was not chosen.

Environment note (2026-11-04): NiceGUI's first import + bind takes minutes on this loaded
shared box — `shoot.py` polls the port instead of a fixed sleep; a bare `ui.run(...)` without
`reload=False, show_welcome_message=False, host=127.0.0.1` hangs silently in a headless shell.
