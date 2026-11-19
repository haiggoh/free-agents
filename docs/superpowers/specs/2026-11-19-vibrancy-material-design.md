# Design: vibrancy material follows the app's light/dark mode (binary), optional system-follow

Status: proposed 2026-11-19, awaiting user approval before planning/execution.

## Intent and why

User report (native window, 2026-11-19): at 0% opacity with native blur on, the background
looks **identical in light and dark app mode** — light mode should read much brighter. Root
cause (verified in the installed pywebview, `platforms/cocoa.py:684-696`): the blur is an
`NSVisualEffectView` added below the webview with `BlendingModeBehindWindow` and **no material
set**, so it uses the system-appearance default material — the app's `dark_mode` never reaches
it. (The user is in system dark mode, which is why both app modes showed the same dark glass.)

Goal: light mode → bright glass, dark mode → dark glass, switching automatically with the
app's dark-mode switch, at ANY opacity including 0%. The opacity slider stays purely the
opacity lever (it already works); the new lever is the vibrancy **material**.

## Goals

1. `dark_mode: false` → vibrancy material `NSVisualEffectMaterialMediumLight` (spike against
   `Light`; pick the brighter-clean one by eye). `dark_mode: true` → `NSVisualEffectMaterialUltraDark`
   (spike against `Dark`/`HUDWindow`).
2. Binary: the material follows the dark-mode switch one-to-one. No continuous brightness slider.
3. Optional new store key `follow_system_appearance` (default `false`): when true, the effective
   dark mode is read from `NSApp.effectiveAppearance` (DarkAqua ⇒ dark) at window creation,
   instead of the stored `dark_mode`. Read once at launch in v1; live system-appearance
   switching (NSApplicationDidChangeEffectiveAppearanceNotification) is phase 2, documented
   here but not in v1.
4. Browser mode unchanged (no window args, no vibrancy — existing behavior).
5. At 100% opacity + blur the background must still read pure white. **The 2026-11-19 tint
   compensation (BLUR_BRIGHTNESS_FACTOR ×1.35 with blur) is REMOVED in this work**: it was a
   hack against the vibrancy darkening, but it compresses the slider's visible range (with
   blur on, actual overlay alpha = opacity × 1.35, so ≥74% already renders fully opaque —
   the user's "above 50% barely translucent" observation). Once the material itself is
   bright in light mode, `tint_alpha` returns the raw opacity in both modes and both
   blur states (100% = alpha 1.0 = pure white anyway); the unit test is updated to pin that.

## Non-goals / anti-goals

- No `WithinWindow` blending experiments in v1 (keep `BehindWindow`).
- No CSS `backdrop-filter` reliance: the effect view sits BELOW the window, so web content
  cannot sample it — a `backdrop-filter: brightness()` layer is expected not to work under a
  transparent window (would need a spike only if the material route fails).
- No per-page or per-control materials.
- No changes to text colors — the existing `dark-shell` CSS tokens already flip those.

## Constraints

- pywebview is pinned inside the GUI venv; the patch wraps its Cocoa `BrowserView.__init__`
  and must **fail open** (any error → unpatched default vibrancy) plus a version check that
  logs once.
- NiceGUI native mode spawns a multiprocessing SPAWN child that re-imports `main.py` as
  `__mp_main__`, so the patch must be applied at `main.py` **module level** (runs in the child
  too), only on the native branch.
- Appearance store schema v1 gains `follow_system_appearance: bool = false` with `_validate`
  coverage; unknown-key preservation already exists.
- Existing tests must stay green: `test_native_window_args_mapping`, appearance store tests,
  browser suites (browser mode never touches vibrancy).

## How (decided)

- New `bin/fa_gui/native_window.py`:
  - `material_for(dark_mode: bool, follow_system: bool) -> int` — pure function returning the
    AppKit material constant; unit-testable without a window. Light → MediumLight, dark →
    UltraDark (pending the spike).
  - `apply_vibrancy_patch()` — wraps `webview.platforms.cocoa.BrowserView.__init__`: call the
    original, then scan `self.webview.subviews` for an `NSVisualEffectView` and
    `setMaterial_(material_for(...))` reading the appearance store (with `LA_GUI_APPEARANCE_FILE`
    honored via `store_path()`). Fail-open on any exception.
- `main.py`: in the native branch (before `ui.run`), call `native_window.apply_vibrancy_patch()`;
  at module level so the SPAWN child re-applies it.
- Appearance store: add `follow_system_appearance` key + validator + docstring; the appearance
  section may get a "Follow system appearance" switch (launch_only, since the window material
  is read at launch).
- Spike first (throwaway): patch + launch the real app native with dark_mode on/off and confirm
  by eye that 0% opacity now differs; screenshots via `screencapture -x` of the window.

## Verification

- Unit tests: `material_for` mapping both ways; `follow_system_appearance` resolution with a
  faked effective appearance; validator accepts/rejects the new key; `native_window_args()`
  unchanged (vibrancy/transparent flags untouched).
- Spike evidence: two screenshots (light/dark at 0% opacity) attached to the plan receipt.
- Full GUI suites green (browser + appearance + unit), branch push-only per the standing rule.

## Left to the builder

- Exact patch mechanics (init wrapper vs post-hoc subview scan), error logging wording.
- Spike choice between the two light and two dark material candidates.
- Whether the appearance-section switch for follow-system appears in v1 or phase 2.
- CHANGELOG entry wording.
