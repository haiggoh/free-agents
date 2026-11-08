# Modern presentation direction — Free Agents GUI menu

Dealt with the design-taste-frontend skill (`draw.mjs --app --sector dev-tool --kind devtools`),
saved at `.taste-skill/app/direction.json`.

**Chosen direction: `achromatic-ledger` (seed 666057615), shell `open-plane`.**
Why: the product's identity lives in the emoji action tiles, so the chrome stays achromatic
(ink/grey) and the tiles carry all the colour; a light neutral ground also reads correctly
under the translucent/vibrancy window. Rejected: `data-terminal` (dark chrome fights the emoji
palette and the light-default), `framed-sheet` (its opaque white sheet would defeat translucency).

## Tokens applied to the GUI CSS (bin/fa_gui/app.py)

- ground `#f8f8f8`, surface `#ffffff`, ink `#141414`, one hue `#1d5fbf` (hover/links only)
- gap 12px everywhere, radius-card 8px, radius-control 6px, body 14px, title 24px
- no shadows at rest (switch knob shadow removed); hover = neutral `#e9e9e9` surface on tiles
- tiles: surface cards on the ground, lanes 2x2 / rest 1x1, emoji above wrapping label
- dark mode: ground `#141414`, text `#f8f8f8` (ink/ground swapped), hover hue `#7ea6e0`

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
tokens (body ground 248,248,248; 14px; tile surface 255,255,255; radius 8px; gap 12px; hover
rgb(29,95,191); no rest shadow). Full GUI-venv gate green. The full-page look still needs the
user's eyes on the real window (canvas screenshots can't run here — no system Chrome; the
playwright chromium could drive inspect.mjs if a pixel review is wanted later).
