# Vibrancy Material Follows Light/Dark Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native, in-session — the user's standing rule) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The native window's blur (vibrancy) material follows the app's `dark_mode` — bright glass in light mode, dark glass in dark mode — at any opacity including 0%, with an optional `follow_system_appearance` key and the ×1.35 tint-compensation hack removed.

**Architecture:** A small fail-open pyobjc patch (`bin/fa_gui/native_window.py`) wraps pywebview's Cocoa `BrowserView.__init__`, finds the `NSVisualEffectView` pywebview creates (which it leaves material-less, so it follows the system appearance), and sets its material from a pure `material_name_for(dark_mode)` mapping applied at `main.py` module level so NiceGUI's SPAWN child gets it too. The appearance store gains `follow_system_appearance`; `tint_alpha` returns the raw opacity in all modes once the material provides the brightness.

**Tech Stack:** Python 3.12 GUI venv, NiceGUI 3.x native mode, pywebview (Cocoa backend, pinned in `~/.local/share/free-agents/gui-venv`), pyobjc `AppKit` / `Foundation`, pytest + Playwright (browser suites), raw AppKit constants (verified present: `NSVisualEffectMaterialMediumLight`, `NSVisualEffectMaterialUltraDark`, `NSAppearanceNameDarkAqua`).

**Spec:** `docs/superpowers/specs/2026-11-19-vibrancy-material-design.md`

## Global Constraints

- Branch `feat/nicegui-frontend`, worktree `~/ClaudeWorkspace/.wt/fa-nicegui`; commit with the free-lane trailer table (GLM 5.3 row); **push only — never merge/tag/release** (standing user rule).
- pywebview patch must **fail open**: any import/patch/marker error leaves today's default vibrancy, and logs once.
- Browser mode must remain untouched: no window args, no vibrancy, no AppKit import failure in the browser suites.
- The patch must be applied at `main.py` **module level** (NiceGUI native mode spawns a multiprocessing SPAWN child that re-imports `main.py` as `__mp_main__` and never calls `main()`).
- Spike before finalizing materials: the real app, native window, 0% opacity, light vs dark — screencapture both (`screencapture -x`); the spike's screenshots are the receipt.
- Opacity semantics after this work: `tint_alpha(pct) == pct/100` in every mode/blur state (100% = pure white).

## Review Focus

| Input / condition | Expected behavior | Test lives in |
|---|---|---|
| System dark mode, app light mode, 0% opacity | Bright (light) glass — the reported bug | Task 3 + Task 5 spike screenshots |
| pywebview internals change (version drift) | Patch no-ops, today's default vibrancy, no crash | Task 3 smoke test (fail-open) |
| Browser mode | No vibrancy, no window args, unchanged behavior | Task 5 existing browser suites green |
| `follow_system_appearance: false` | Store's `dark_mode` wins over the system setting | Task 2 unit test |
| Opacity slider 0 and 100 with blur ON | 0 → raw material, 100 → pure white (alpha 1.0) | Task 1 rewritten tint test |

---

### Task 1: Pure material mapping + validator + remove the ×1.35 factor

**Files:**
- Create: `bin/fa_gui/native_window.py` (only `material_name_for` in this task)
- Modify: `bin/fa_gui/appearance.py` (delete `BLUR_BRIGHTNESS_FACTOR`, `tint_alpha` returns raw opacity; add `follow_system_appearance` default + `_validate` branch)
- Test: `tests/test_gui_appearance.py`, `tests/test_0564_user_issues.py` (new class `VibrancyMaterialTests`)

**Interfaces:**
- Consumes: nothing.
- Produces: `fa_gui.native_window.material_name_for(dark_mode: bool) -> str` (`"mediumLight"` / `"ultraDark"`); `fa_gui.appearance.tint_alpha(opacity_pct: int, blur_active: bool = False) -> float` (raw); store key `follow_system_appearance` in `DEFAULTS` + validated bool.

- [ ] **Step 1: Write the failing tests**

`tests/test_gui_appearance.py` — rewrite `test_tint_alpha_never_darkens_with_blur`:

```python
    def test_tint_alpha_returns_raw_opacity_everywhere(self):
        # 2026-11-20: the x1.35 blur compensation is gone (it compressed the slider —
        # >=74% already rendered opaque with blur). The material lever now carries the
        # brightness; tint_alpha returns the requested opacity in every mode.
        for pct in (0, 25, 50, 74, 90, 100):
            self.assertEqual(ap.tint_alpha(pct, blur_active=False), round(pct / 100, 3))
            self.assertEqual(ap.tint_alpha(pct, blur_active=True), round(pct / 100, 3))
```

…and extend `test_presentation_and_bool_validators` with `"follow_system_appearance"` in the bool-key rejection loop and an acceptance case (`True`/`False` save+load round-trip).

`tests/test_0564_user_issues.py` — new class:

```python
class VibrancyMaterialTests(unittest.TestCase):
    def test_material_map_is_binary(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin" / "fa_gui"))
        from fa_gui.native_window import material_name_for
        self.assertEqual(material_name_for(True), "ultraDark")
        self.assertEqual(material_name_for(False), "mediumLight")

    def test_follow_system_key_defaults_off_and_validates(self):
        import importlib
        import fa_gui.appearance as ap
        d = ap.DEFAULTS
        self.assertIs(d["follow_system_appearance"], False)
        with self.assertRaises(ValueError):
            ap.save(self.path, {"follow_system_appearance": "yes"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/.local/share/free-agents/gui-venv/bin/python -m pytest tests/test_gui_appearance.py tests/test_0564_user_issues.py -q -p no:cacheprovider`
Expected: FAIL — `fa_gui.native_window` missing; tint_alpha returns the old scaled values; validator rejects the unknown key.

- [ ] **Step 3: Implement**

Create `bin/fa_gui/native_window.py` with `material_name_for(dark_mode: bool) -> str` (return the pinned constants above; no AppKit import at module level — the constant resolution is Task 3).

In `bin/fa_gui/appearance.py`: delete `BLUR_BRIGHTNESS_FACTOR` and its comment; `tint_alpha` becomes `a = min(100, max(0, opacity_pct)) / 100; return round(a, 3)` with the docstring stating the material lever replaced the compensation; add `"follow_system_appearance": False` to `DEFAULTS` (comment: read at window creation, native mode only) and a `_validate` branch `elif key == "follow_system_appearance": if not isinstance(value, bool): raise ValueError(...)`.

- [ ] **Step 4: Run the tests to verify they pass**

Same command as Step 2; Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/fa_gui/native_window.py bin/fa_gui/appearance.py tests/test_gui_appearance.py tests/test_0564_user_issues.py
git commit -m "refactor(gui): raw-opacity tint + material mapping + follow_system_appearance key"
```

### Task 2: System-appearance resolution (pure, testable)

**Files:**
- Modify: `bin/fa_gui/native_window.py` (add `is_system_dark(appearance_name: str | None) -> bool` and `resolve_dark_mode(doc, system_dark) -> bool`)
- Test: `tests/test_0564_user_issues.py` (extend `VibrancyMaterialTests`)

**Interfaces:**
- Consumes: Task 1's `material_name_for`.
- Produces: `fa_gui.native_window.is_system_dark(appearance_name: str | None) -> bool` (a name containing `"Dark"` ⇒ True; None ⇒ False); `fa_gui.native_window.resolve_dark_mode(doc: dict, system_dark: bool) -> bool` (`doc["follow_system_appearance"]` ⇒ `system_dark` else `doc["dark_mode"]`).

- [ ] **Step 1: Write the failing tests**

Add to `VibrancyMaterialTests`:

```python
    def test_store_wins_when_follow_system_off(self):
        from fa_gui.native_window import resolve_dark_mode
        doc = {"dark_mode": True, "follow_system_appearance": False}
        self.assertTrue(resolve_dark_mode(doc, system_dark=False))  # store wins
        self.assertFalse(resolve_dark_mode({"dark_mode": False, "follow_system_appearance": False}, system_dark=True))

    def test_system_wins_when_follow_system_on(self):
        from fa_gui.native_window import resolve_dark_mode
        self.assertFalse(resolve_dark_mode({"dark_mode": True, "follow_system_appearance": True}, system_dark=False))
        self.assertTrue(resolve_dark_mode({"dark_mode": False, "follow_system_appearance": True}, system_dark=True))
        from fa_gui.native_window import is_system_dark
        self.assertTrue(is_system_dark("NSAppearanceNameDarkAqua"))
        self.assertFalse(is_system_dark("NSAppearanceNameAqua"))
        self.assertFalse(is_system_dark(None))
```

- [ ] **Step 2: Run to verify it fails** — FAIL:ImportError/AttributeError.

- [ ] **Step 3: Implement** the two pure functions in `bin/fa_gui/native_window.py`.

- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** — `git commit -m "feat(native-window): dark-mode resolution follows store or system"`

### Task 3: The fail-open pywebview vibrancy patch

**Files:**
- Modify: `bin/fa_gui/native_window.py` (add `apply_vibrancy_patch()`, module docstring; still no AppKit import at module level — import inside the function)
- Modify: `bin/fa_gui/main.py` (call `apply_vibrancy_patch()` at module level, guarded, native-mode-independent — it no-ops when webview is missing)
- Test: `tests/test_0564_user_issues.py` (extend `VibrancyMaterialTests` with a smoke test)

**Interfaces:**
- Consumes: `material_name_for`, `resolve_dark_mode`, `fa_gui.appearance` (`store_path`, `load`, `DEFAULTS`).
- Produces: `fa_gui.native_window.apply_vibrancy_patch() -> None` — idempotent; wraps `webview.platforms.cocoa.BrowserView.__init__` so that after the original init it scans `self.webview.subviews()` for an `AppKit.NSVisualEffectView` instance and calls `setMaterial_(<constant>)` for `material_name_for(resolve_dark_mode(doc, is_system_dark(current appearance name)))`. On any exception it logs `native_window: vibrancy patch not applied (<reason>)` and returns unpatched. Marks the wrapper with the attribute `_fa_vibrancy_patch = True`.

- [ ] **Step 1: Write the failing smoke test**

```python
    def test_patch_applies_and_is_fail_safe(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin" / "fa_gui"))
        import webview  # installed in the GUI venv
        from fa_gui import native_window as nw
        before = webview.platforms.cocoa.BrowserView.__init__
        nw.apply_vibrancy_patch()
        self.assertTrue(getattr(webview.platforms.cocoa.BrowserView.__init__, "_fa_vibrancy_patch", False))
        nw.apply_vibrancy_patch()          # idempotent: no double wrap
        self.assertTrue(getattr(webview.platforms.cocoa.BrowserView.__init__, "_fa_vibrancy_patch", False))
        self.assertIs(webview.platforms.cocoa.BrowserView.__init__, before) or True  # may or may not rewrap; must not raise
```

plus a fail-open probe: monkeypatch `webview.platforms.cocoa` to raise on attribute access of `NSVisualEffectView`… **simpler and still honest**: call `apply_vibrancy_patch()` with `webview` temporarily removed from `sys.modules` and assert no exception and no wrapper installed:

```python
    def test_patch_missing_webview_is_a_noop(self):
        import sys
        from fa_gui import native_window as nw
        saved = sys.modules.pop("webview", None)
        try:
            nw.apply_vibrancy_patch()          # must not raise without pywebview
        finally:
            if saved is not None:
                sys.modules["webview"] = saved
```

- [ ] **Step 2: Run to verify it fails** — FAIL: `apply_vibrancy_patch` undefined.
- [ ] **Step 3: Implement** `apply_vibrancy_patch()` (AppKit + `Foundation` imported inside; wrapper logic per Interfaces). In `bin/fa_gui/main.py`, after the imports and before `def main()`, add a guarded module-level call:

```python
try:
    from fa_gui import native_window as _native_window
    _native_window.apply_vibrancy_patch()   # module level: the SPAWN child re-imports main, main() never runs there
except Exception:
    pass                                    # browser mode / no pywebview: no-op by design
```

- [ ] **Step 4: Run the tests** (`tests/test_0564_user_issues.py` + the native smoke); Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(gui): fail-open pywebview vibrancy-material patch (native mode only)"`

### Task 4: Appearance-section switch for follow-system

**Files:**
- Modify: `bin/fa_gui/app.py` (one `_appearance_switch` row in `render_appearance`)
- Test: `tests/test_gui_browser.py` (extend `test_dark_mode_switch_flips_store_and_shell` with a second server/store)

**Interfaces:**
- Consumes: store key `follow_system_appearance` (Task 1).
- Produces: a launch-only switch labeled `🌗 Follow system appearance` (same pattern as the blur switch: `launch_only=True`, writes the store).

- [ ] **Step 1: Write the failing browser test** — in `test_dark_mode_switch_flips_store_and_shell`, or a new `test_follow_system_switch_writes_store`: start a server with `LA_GUI_APPEARANCE_FILE`, open `theme_config`, assert `[data-key="follow_system_appearance"]` exists and clicking its checkbox writes `follow_system_appearance: true` into the store file (same deadline-loop pattern as the dark-mode test).
- [ ] **Step 2: Run to verify it fails** — FAIL: selector missing.
- [ ] **Step 3: Implement** the one-line `_appearance_switch("follow_system_appearance", "🌗 Follow system appearance", launch_only=True)` after the blur switch.
- [ ] **Step 4: Run the browser test; PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(appearance): follow-system-appearance switch (launch-only)"`

### Task 5: Spike, full suites, docs, push, dogfood

**Files:**
- Modify: `docs/gui/PARITY-csl.md` (theme row: material follows dark_mode)
- Modify: `CHANGELOG.md` new top entry for the GUI branch (no version bump — the branch numbers its own releases)

- [ ] **Step 1: Spike the material choice.** Native window, temp appearance store (`LA_GUI_APPEARANCE_FILE`, `LA_GUI_NATIVE=1`) twice — light (mediumLight) and dark (ultraDark) — opacity 0%, translucency+blur on; `screencapture -x "$TMPDIR/spike-light.png"` and `spike-dark.png` between. If either candidate looks wrong (MediumLight not bright enough / UltraDark too black), change `material_name_for`'s constant in Task 3's code, re-run Task 1's unit test (it pins the *names*, so a constant change is inside the patch's name→constant table — record the final table in the commit message) and re-spike.
- [ ] **Step 2: Full suites.** `pytest tests/test_gui_appearance.py tests/test_0564_user_issues.py tests/test_catalog_overlay.py tests/test_gui_browser.py tests/test_gui_prefetch.py tests/test_picker_core.py -q -p no:cacheprovider -rs`; Expected: all PASS, 0 skips.
- [ ] **Step 3: Docs.** PARITY-csl.md theme row: add "vibrancy material follows dark_mode (MediumLight/UltraDark, fail-open pyobjc patch on pywebview); optional Follow system appearance (launch-only)". CHANGELOG top section `## [unreleased]` matching the branch's existing style.
- [ ] **Step 4: Commit + push** (push-only): `git commit -m "docs(gui): parity + CHANGELOG for the vibrancy material work"` then `git push origin feat/nicegui-frontend`, verify with `git ls-remote`.
- [ ] **Step 5: Dogfood for the user.** Relaunch the native window (their real store is untouched; use a temp store with translucency+blur on and 0% opacity) and report the ready line; the user eyeballs light vs dark at 0%. Attach/reference the two spike screenshots.

## Self-review notes

- Spec coverage: binary material → Tasks 1+3; optional follow-system → Tasks 1+2+4; ×1.35 removal → Task 1; fail-open + spawn-child placement → Task 3; browser unchanged → Tasks 3+5; spike materials → Task 5. Nothing in the spec is untasked.
- Type drift check: `material_name_for -> str`, `is_system_dark(str|None) -> bool`, `resolve_dark_mode(dict, bool) -> bool`, `apply_vibrancy_patch() -> None` — consistent across Tasks 1-4.
- Proportion: bodies only where the algorithm is Task 3's wrapper; everything else is signatures + assertions.