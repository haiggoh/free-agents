"""fa_gui/appearance.py — the GUI-only appearance store (continuation plan Phase 4).

The semantic store (session_menu_state) REFUSES unknown sections by design, so GUI appearance
never goes there: it lives in its own small validated file, default
~/.claude/free-agents/gui-appearance.json (LA_GUI_APPEARANCE_FILE overrides; a SET but
missing-parent path is a hard error, never a silent fallthrough).

Contract (plan section 4): defaults when the file is absent; load NEVER writes; strict
validators reject bad values BEFORE any write; saves are field-level atomic merges that
preserve unknown keys; a future-schema file is warned about and left byte-identical. NaN and
non-finite numbers are rejected at parse time.
"""
from __future__ import annotations

import json
import math
import os
import warnings
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

DEFAULTS: dict[str, Any] = {
    "presentation": "compatibility",          # "compatibility" | "modern"
    "background_translucent": False,          # white translucency opt-in (Phase 6 enables it)
    # background_opacity_pct has NO fixed default (user ruling 2026-11-08): absent = auto —
    # resolve_opacity() picks 80% when native blur is active, 95% when it is unavailable
    # (50 was unreadable without blur). An explicit saved value always wins.
    "background_blur_requested": False,       # request flag only — never proof blur is active
    "dark_mode": False,                       # Phase 8, LAST; initial light
}

DEFAULT_OPACITY_BLUR_ACTIVE = 80
DEFAULT_OPACITY_NO_BLUR = 95


def resolve_opacity(doc: dict, blur_active: bool = False) -> int:
    """The effective tint opacity: an explicitly saved background_opacity_pct wins; otherwise
    the capability-dependent default (80% with active native blur, 95% without)."""
    v = doc.get("background_opacity_pct")
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return DEFAULT_OPACITY_BLUR_ACTIVE if blur_active else DEFAULT_OPACITY_NO_BLUR

PRESENTATIONS = ("compatibility", "modern")
_OPACITY_MIN, _OPACITY_MAX = 50, 100


def store_path(env=None) -> Path:
    """The appearance file: LA_GUI_APPEARANCE_FILE when set (parent dir must exist — a set
    but invalid override aborts, never falls through), else the stable user store."""
    env = os.environ if env is None else env
    override = env.get("LA_GUI_APPEARANCE_FILE")
    if override:
        p = Path(override)
        if not p.parent.is_dir():
            raise RuntimeError(f"LA_GUI_APPEARANCE_FILE is set but its directory does not "
                               f"exist: {p.parent}")
        return p
    return Path.home() / ".claude" / "free-agents" / "gui-appearance.json"


def _reject_constant(value: str):
    raise ValueError(f"non-finite JSON constant {value!r} is not allowed in the appearance store")


def _validate(key: str, value: Any) -> None:
    if key == "presentation":
        if value not in PRESENTATIONS:
            raise ValueError(f"presentation must be one of {PRESENTATIONS}, got {value!r}")
    elif key in ("background_translucent", "background_blur_requested", "dark_mode"):
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be a bool, got {value!r}")
    elif key == "background_opacity_pct":
        if value is None:
            raise ValueError("background_opacity_pct=None means auto — never save it; "
                             "save the resolved int")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"background_opacity_pct must be an int, got {value!r}")
        if not math.isfinite(value) or not (_OPACITY_MIN <= value <= _OPACITY_MAX):
            raise ValueError(f"background_opacity_pct must be {_OPACITY_MIN}..{_OPACITY_MAX}, "
                             f"got {value!r}")
    elif key == "schema_version":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"schema_version must be an int, got {value!r}")
    # unknown keys are carried, not validated (future-schema protection lives on the version)


def load(path: Path) -> dict:
    """Read the store, merged over DEFAULTS. Absent/invalid/future-schema -> defaults (with a
    warning), and the file is NEVER written or rewritten by a load."""
    path = Path(path)
    if not path.is_file():
        return dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, ValueError) as err:
        warnings.warn(f"gui appearance file unreadable/invalid ({err}); using defaults, "
                      f"leaving {path} untouched")
        return dict(DEFAULTS)
    if not isinstance(raw, dict):
        warnings.warn(f"gui appearance file is not an object; using defaults, leaving {path} untouched")
        return dict(DEFAULTS)
    version = raw.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or isinstance(version, bool) or version > SCHEMA_VERSION:
        warnings.warn(f"gui appearance file has schema {version!r} (known {SCHEMA_VERSION}); "
                      f"using defaults, leaving {path} untouched")
        return dict(DEFAULTS)
    doc = dict(DEFAULTS)
    doc.update(raw)                     # unknown keys carried
    try:
        for key, value in doc.items():
            # opacity is known-but-optional (auto when absent): validate it only when present
            if key in DEFAULTS or key == "schema_version" or (key == "background_opacity_pct" and value is not None):
                _validate(key, value)   # a known key with a bad value
    except ValueError as err:
        # review finding: an invalid KNOWN key must warn + default, not crash the GUI at load
        warnings.warn(f"gui appearance file has an invalid value ({err}); using defaults, "
                      f"leaving {path} untouched")
        return dict(DEFAULTS)
    return doc


def native_window_args(env=None) -> dict:
    """Phase 6: window args for pywebview's Cocoa backend (installed source supports
    window.transparent and window.vibrancy = NSVisualEffectView behind-window). Returned ONLY
    for native mode — main.py never passes them to ui.run in browser mode, so no browser
    session implies a physical desktop blur. Empty dict = fully opaque default window."""
    doc = load(store_path(env))
    args: dict = {}
    if doc["background_translucent"]:
        args["transparent"] = True
    if doc["background_blur_requested"]:
        args["vibrancy"] = True
    return args


def save(path: Path, patch: dict) -> dict:
    """Validate the patch FIRST (nothing is written on a bad value), then field-level merge
    into the current file atomically (tmp + os.replace in the same directory), preserving
    unknown keys. Returns the full new document."""
    path = Path(path)
    for key, value in patch.items():
        _validate(key, value)
    current = load(path)
    current.update(patch)
    current["schema_version"] = SCHEMA_VERSION
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return current
