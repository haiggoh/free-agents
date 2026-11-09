#!/usr/bin/env python3
"""tests/test_gui_appearance.py — continuation-plan Phase 4: the GUI-only appearance store.

The semantic store (session_menu_state) refuses unknown sections, so GUI appearance lives in
its OWN validated file (never duplicating semantic preferences, never loosening schema
protection). Contract: defaults when absent, load never writes, strict validators reject
bad values BEFORE any write, field-level atomic merge that preserves unknown keys, and a
future-schema file is warned about and left untouched.

Usage: python3 tests/test_gui_appearance.py [-v]   (also runs under pytest; no GUI venv needed)
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from fa_gui import appearance as ap  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "gui-appearance.json"

    def test_absent_file_loads_defaults_without_writing(self):
        doc = ap.load(self.path)
        self.assertEqual(doc, ap.DEFAULTS)
        self.assertFalse(self.path.exists(), "load must never write")

    def test_round_trip_and_unknown_keys_preserved(self):
        ap.save(self.path, {"presentation": "modern", "future_key": {"x": 1}})
        doc = ap.load(self.path)
        self.assertEqual(doc["presentation"], "modern")
        self.assertEqual(doc["future_key"], {"x": 1}, "unknown keys survive a field-level save")
        self.assertNotIn("background_opacity_pct", doc,
                         "opacity has no fixed default — absent means capability-auto")
        self.assertEqual(list(Path(self.tmp.name).glob("*.tmp*")), [], "atomic save leaves no tmp")

    def test_opacity_bounds_and_types(self):
        for bad in (49, 101, 50.5, True, "80"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ap.save(self.path, {"background_opacity_pct": bad})
        with self.assertRaises(ValueError):
            ap.save(self.path, {"background_opacity_pct": None})   # None = auto, not savable
        self.assertFalse(self.path.exists(), "a rejected write must not create the file")
        for good in (50, 75, 100):
            ap.save(self.path, {"background_opacity_pct": good})
            self.assertEqual(ap.load(self.path)["background_opacity_pct"], good)

    def test_presentation_and_bool_validators(self):
        for bad in ("Modern", "dark", 1, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ap.save(self.path, {"presentation": bad})
        for bad in ("yes", 0, None, 1.5):
            with self.subTest(flag=bad):
                with self.assertRaises(ValueError):
                    ap.save(self.path, {"background_translucent": bad})
                with self.assertRaises(ValueError):
                    ap.save(self.path, {"background_blur_requested": bad})
                with self.assertRaises(ValueError):
                    ap.save(self.path, {"dark_mode": bad})
        self.assertFalse(self.path.exists())

    def test_nan_and_nonfinite_rejected(self):
        # NaN is rejected at PARSE time on load (warn + defaults, file untouched — the store's
        # unreadable/invalid policy); on SAVE the validator rejects it before any write.
        raw = '{"schema_version": 1, "background_opacity_pct": NaN}'
        self.path.write_text(raw)
        with self.assertWarns(UserWarning):
            doc = ap.load(self.path)
        self.assertNotIn("background_opacity_pct", doc)   # NaN file -> defaults, no opacity key
        self.assertEqual(self.path.read_text(), raw, "an invalid file is left untouched")
        with self.assertRaises(ValueError):
            ap.save(self.path, {"background_opacity_pct": float("nan")})

    def test_future_schema_warns_and_loads_defaults_untouched(self):
        raw = '{"schema_version": 99, "presentation": "modern"}'
        self.path.write_text(raw)
        with self.assertWarns(UserWarning):
            doc = ap.load(self.path)
        self.assertEqual(doc, ap.DEFAULTS, "future schema -> defaults, not the file's values")
        self.assertEqual(self.path.read_text(), raw, "file untouched")

    def test_invalid_known_key_warns_and_defaults_untouched(self):
        # review finding 1.2/5.1: a VALID-schema file with a bad known-key value (or a bool
        # schema_version, which is an int in Python) must warn + default, never crash load
        for raw in ('{"schema_version": true, "dark_mode": "yes"}',
                    '{"schema_version": 1, "background_opacity_pct": 500}'):
            with self.subTest(raw=raw):
                self.path.write_text(raw)
                with self.assertWarns(UserWarning):
                    doc = ap.load(self.path)
                self.assertEqual(doc, ap.DEFAULTS)
                self.assertEqual(self.path.read_text(), raw, "invalid file left byte-identical")

    def test_env_override_path(self):
        d = Path(self.tmp.name) / "envdir"
        d.mkdir()
        p = ap.store_path({"LA_GUI_APPEARANCE_FILE": str(d / "a.json")})
        self.assertEqual(p, d / "a.json")
        with self.assertRaises(RuntimeError):
            ap.store_path({"LA_GUI_APPEARANCE_FILE": str(d / "nope" / "a.json")})

    def test_tint_alpha_never_darkens_with_blur(self):
        # user 2026-11-08: with blur on, 50% translucency must feel as bright as 85% without
        # blur -> blur active adds +35 points of effective brightness
        self.assertEqual(ap.tint_alpha(95, blur_active=False), 0.95)
        self.assertEqual(ap.tint_alpha(50, blur_active=False), 0.5)
        self.assertEqual(ap.tint_alpha(100, blur_active=False), 1.0)
        self.assertEqual(ap.tint_alpha(50, blur_active=True), 0.05,
                         "with blur ON the tint starts near 0 — the vibrancy material is the surface")
        # the slider must still respond with blur on (user 2026-11-09), never exceeding +0.30
        self.assertEqual(ap.tint_alpha(100, blur_active=True), 0.35)
        self.assertLess(ap.tint_alpha(60, True), ap.tint_alpha(90, True))
        # without blur: the requested opacity applies unchanged; with blur never darker
        self.assertLess(ap.tint_alpha(60, False), ap.tint_alpha(90, False))
        for pct in (50, 60, 70, 80, 90, 100):
            self.assertLessEqual(ap.tint_alpha(pct, True), ap.tint_alpha(pct, False))

    def test_native_window_args_mapping(self):
        # Phase 6: translucent -> transparent window; blur requested -> vibrancy (built-in
        # NSVisualEffectView behind-window). Browser mode never sees these (main.py only
        # passes them to ui.run in native mode). Defaults -> no args at all.
        d = Path(self.tmp.name)
        f = d / "a.json"
        env = {"LA_GUI_APPEARANCE_FILE": str(f)}
        self.assertEqual(ap.native_window_args(env), {})
        ap.save(f, {"background_translucent": True})
        self.assertEqual(ap.native_window_args(env), {"transparent": True})
        ap.save(f, {"background_blur_requested": True})
        self.assertEqual(ap.native_window_args(env),
                         {"transparent": True, "vibrancy": True})
        ap.save(f, {"background_translucent": False})
        self.assertEqual(ap.native_window_args(env), {"vibrancy": True},
                         "blur request without translucency still maps to vibrancy")
        ap.save(f, {"background_blur_requested": False})
        self.assertEqual(ap.native_window_args(env), {})

    def test_defaults_shape_matches_plan(self):
        # plan section 4: presentation compatibility|modern, translucency bool, blur request
        # bool, dark last (initial false/light). Opacity has NO fixed default — the user
        # ruling 2026-11-08: 95% when blur is unavailable, 80% when blur is active.
        self.assertEqual(ap.DEFAULTS["presentation"], "compatibility")
        self.assertIs(ap.DEFAULTS["background_translucent"], False)
        self.assertNotIn("background_opacity_pct", ap.DEFAULTS)
        self.assertIs(ap.DEFAULTS["background_blur_requested"], False)
        self.assertIs(ap.DEFAULTS["dark_mode"], False)
        self.assertEqual(ap.SCHEMA_VERSION, 1)

    def test_opacity_default_is_capability_dependent(self):
        # user 2026-11-08: with blur on, 50% is the default; without blur, 95%
        doc = ap.load(self.path)          # absent file -> defaults, no opacity key
        self.assertNotIn("background_opacity_pct", doc)
        self.assertEqual(ap.resolve_opacity(doc, blur_active=False), 95)
        self.assertEqual(ap.resolve_opacity(doc, blur_active=True), 50)
        ap.save(self.path, {"background_opacity_pct": 60})
        doc = ap.load(self.path)
        self.assertEqual(ap.resolve_opacity(doc, blur_active=True), 60,
                         "an explicit saved opacity always wins over the capability default")
        self.assertEqual(ap.resolve_opacity(doc, blur_active=False), 60)


if __name__ == "__main__":
    unittest.main()
