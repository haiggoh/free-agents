#!/usr/bin/env python3
"""Where the per-user LOCAL model-catalog overlay lives (0.58.3, user 2026-11-14).

The Python twin of bin/la-catalog-overlay.sh — same resolution order, parity-tested in
tests/test_catalog_overlay.py:
  1. $LA_MODEL_CATALOG_LOCAL — explicit file; set but missing raises OverlayOverrideError
  2. ~/.claude/free-agents/model-catalog.local.psv — the per-user store every copy reads
  3. <config_dir>/model-catalog.local.psv — the legacy location
  4. None

Usage as a script:  catalog_overlay.py [--config-dir DIR]   (prints the path or nothing)
Environment: LA_MODEL_CATALOG_LOCAL (step 1). The migration lives in the shell helper only.
"""
import os
import sys
from pathlib import Path

NAME = "model-catalog.local.psv"


class OverlayOverrideError(ValueError):
    """LA_MODEL_CATALOG_LOCAL is set but names no file."""


def store_path() -> Path:
    return Path.home() / ".claude" / "free-agents" / NAME


def overlay_path(config_dir: Path) -> "Path | None":
    override = os.environ.get("LA_MODEL_CATALOG_LOCAL")
    if override:
        p = Path(override)
        if p.is_file():
            return p
        raise OverlayOverrideError(f"la-catalog-overlay: LA_MODEL_CATALOG_LOCAL={override} does not exist")
    store = store_path()
    if store.is_file():
        return store
    legacy = Path(config_dir) / NAME
    return legacy if legacy.is_file() else None


def main(argv: list) -> int:
    cfg = Path(__file__).resolve().parents[1] / "config"
    args = list(argv)
    while args:
        a = args.pop(0)
        if a in ("-h", "--help"):
            print((__doc__ or "").strip())
            return 0
        if a == "--config-dir" and args:
            cfg = Path(args.pop(0))
            continue
        print(f"catalog_overlay.py: unrecognised argument {a}\nusage: catalog_overlay.py [--config-dir DIR]",
              file=sys.stderr)
        return 2
    try:
        p = overlay_path(cfg)
    except OverlayOverrideError as e:
        print(e, file=sys.stderr)
        return 2
    print(p or "", end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
