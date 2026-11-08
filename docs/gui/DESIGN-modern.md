# Modern presentation direction — Free Agents GUI menu

Dealt with the design-taste-frontend skill (`draw.mjs --app --sector dev-tool --kind devtools`),
saved at `.taste-skill/app/direction.json`.

**Chosen direction: `achromatic-ledger` (seed 666057615), shell `open-plane`.**
Why: the product's identity lives in the emoji action tiles, so the chrome stays achromatic
(ink/grey) and the tiles carry all the colour; a light neutral ground also reads correctly
under the translucent/vibrancy window. Rejected: `data-terminal` (dark chrome fights the emoji
palette and the light-default), `framed-sheet` (its opaque white sheet would defeat translucency).

## Tokens applied to the GUI CSS (bin/fa_gui/app.py)

- ground `#ffffff` (clean white, user 2026-11-08 — restored after the #f8f8f8 experiment),
  surface `#ffffff`, ink `#141414`
- csl accents integrated (user 2026-11-08): hover/links `#00BFFF` (csl Sky Blue), focus ring
  `#00FFFF` (csl Cyan); tile hover = flat `#f0f9ff` sky tint with sky border (no gradients)
- gap 12px, tile radius 16px (M3-expressive), body 14px, title 24px; no shadows at rest
- dark mode stays a switchable option (dark_mode in the appearance store): ground `#141414`,
  text `#f8f8f8`

## Metro tile layout (user spec 2026-11-08, M3-blended)

- fixed 150px square grid cells (`repeat(auto-fill, 150px)`, `grid-auto-rows: 150px`) so a
  2x2 lane tile is exactly square (bounding-box asserted in tests)
- Local / Remote lanes = `tile-lg` 2x2; **Lowkey = `tile-wide` 2x1** (not the biggest tile)
- emoji is its own flex line, centered above the label, and **peeks out of the tile's top
  edge** (absolute, `top: -1.15em`); label wraps (`overflow-wrap: anywhere`); everything
  larger (cells 150px, emoji 3rem / 4.5rem on big tiles, label 16px)
- translucency chain intact: white `#tint-layer` at the capability-dependent opacity
  (80% with active native blur, 95% without), window args native-only, relaunch applies

## Deliberate deviations from the dealt sheet (recorded per the skill's rules)

- **Emoji kept**: the sheet's house rules ban emoji, but the user brief pins emoji tiles as
  the menu's content language (csl parity) — the brief wins; emoji stay as tile glyphs only,
  never as UI decoration elsewhere.
- **System font stack, not Geist**: a native webview should use platform fonts; the emoji font
  stack stays. Type scale/sizes from the sheet are applied.
- Compatibility list mode is untouched by all of this except the shared tokens (ground/ink/
  hue/body size), which are the app style for both presentations.

## Verification

`tests/test_gui_playwright.py::test_dealt_direction_tokens_applied` asserts the computed
tokens (body ground 255,255,255; 14px; tile surface white; radius 16px; gap 12px; hover
rgb(0,191,255); no rest shadow), and `test_modern_metro_grid_layout` asserts the grid
(columns, 2x2-square lane tiles, 2x1 Lowkey, emoji above + centered + peeking past the tile
top, wrapping label, no grid in compat). Full GUI-venv gate green (77/266, playwright 27/27).
The full-page look still needs the user's eyes on the real window (no system Chrome here;
playwright's chromium could drive inspect.mjs for a pixel review later).
