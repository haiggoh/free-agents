"""fa_gui/native_window.py — native-window vibrancy material follows the app's dark mode.

2026-11-20 spec: docs/superpowers/specs/2026-11-19-vibrancy-material-design.md

pywebview's Cocoa backend creates the blur as an NSVisualEffectView added BELOW the webview
(BlendingModeBehindWindow) and never sets a material, so it follows the SYSTEM appearance —
which is why the app's light/dark mode looked identical at 0% opacity (user was in system
dark mode). This module provides:

- material_name_for(dark_mode) — the pure, unit-testable mapping to material constant NAMES
  (constants are resolved against AppKit only inside apply_vibrancy_patch, so importing this
  module never requires pyobjc — browser mode and the plain pytest runs stay clean).
- apply_vibrancy_patch() — wraps pywebview's cocoa BrowserView.__init__ so that after the
  original init it finds the NSVisualEffectView in the webview's subviews and sets its
  material from the appearance store. FAIL-OPEN: any error logs once and leaves the default
  vibrancy untouched. Idempotent. Call from main.py at MODULE level (NiceGUI's native mode
  re-imports main as __mp_main__ in the spawn child, where main() never runs).
"""

# Spike-pending candidates, chosen by eye in Task 5 (screenshots at 0% opacity):
# light: "light" | "mediumLight"   dark: "dark" | "ultraDark" | "hudWindow"
MATERIAL_BY_NAME = {
    "light": "NSVisualEffectMaterialLight",
    "mediumLight": "NSVisualEffectMaterialMediumLight",
    "dark": "NSVisualEffectMaterialDark",
    "ultraDark": "NSVisualEffectMaterialUltraDark",
    "hudWindow": "NSVisualEffectMaterialHUDWindow",
}


def material_name_for(dark_mode: bool) -> str:
    """The vibrancy material NAME for the app's dark_mode (binary). 2026-11-20 user
    iteration: at 0% opacity MediumLight vs UltraDark read too close — light mode takes
    the brightest material (Light); dark stays at the darkest (UltraDark)."""
    return "ultraDark" if dark_mode else "light"


def is_system_dark(appearance_name: "str | None") -> bool:
    """NSAppearance name containing 'Dark' (e.g. NSAppearanceNameDarkAqua) means system dark."""
    return bool(appearance_name) and "Dark" in appearance_name


def resolve_dark_mode(doc: dict, system_dark: bool) -> bool:
    """follow_system_appearance=true -> the OS decides; otherwise the stored dark_mode wins."""
    if doc.get("follow_system_appearance"):
        return system_dark
    return bool(doc.get("dark_mode"))


def apply_vibrancy_patch() -> None:
    """Wrap pywebview's Cocoa BrowserView init to set the vibrancy material from the
    appearance store. No-op (and never raises) when pywebview is absent or the internals
    moved — default vibrancy stays. Idempotent."""
    try:
        import webview  # noqa: WPS433  (local import: browser mode must not need pywebview)
        from webview.platforms import cocoa
        import AppKit  # noqa: WPS433

        if getattr(cocoa.BrowserView.__init__, "_fa_vibrancy_patch", False):
            return  # already wrapped
        original_init = cocoa.BrowserView.__init__

        def _patched_init(self, window, *args, **kwargs):
            original_init(self, window, *args, **kwargs)
            try:
                from fa_gui import appearance as ap
                doc = ap.load(ap.store_path())
                try:
                    app = AppKit.NSApplication.sharedApplication()
                    sys_dark = is_system_dark(app.effectiveAppearance().name())
                except Exception:
                    sys_dark = False
                dark = resolve_dark_mode(doc, sys_dark)
                const_name = MATERIAL_BY_NAME[material_name_for(dark)]
                material = getattr(AppKit, const_name)
                for sub in self.webview.subviews():
                    if isinstance(sub, AppKit.NSVisualEffectView):
                        sub.setMaterial_(material)
            except Exception as e:  # fail-open: default vibrancy, log once
                print(f"native_window: vibrancy patch not applied ({e!r})", flush=True)

        _patched_init._fa_vibrancy_patch = True
        cocoa.BrowserView.__init__ = _patched_init
    except Exception as e:
        print(f"native_window: vibrancy patch skipped ({e!r})", flush=True)
