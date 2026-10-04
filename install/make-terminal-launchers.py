#!/usr/bin/env python3
"""make-terminal-launchers.py — write double-clickable .terminal launchers for the picker.

A `.command` file makes Terminal open an INTERACTIVE login shell, print "Last login …" and type
`<path> ; exit;` before the script even starts. A `.terminal` file whose settings set
CommandString with RunCommandAsShell=false runs the program as the window's process directly:
no prompt, no banner, no typed command — the first thing on screen is the picker's ⏳ frame.

The window settings are copied from the user's default Terminal profile, so fonts and colours
match their other windows; only the command and window title are set.

Usage:  make-terminal-launchers.py [--out DIR] [--profile NAME] [--dry-run]
        DIR defaults to ~/Apps. Existing files are never overwritten (exit 1 instead).
Env:    none.
"""
import argparse
import plistlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHERS = {
    "Free Agents — Session Picker.terminal": (REPO / "bin/csl", "Free Agents — Session Picker"),
    "Free Agents — Remote Sessions.terminal": (REPO / "bin/remote-session.sh", "Free Agents — Remote"),
}


def profile(name: str | None) -> dict:
    prefs = Path.home() / "Library/Preferences/com.apple.Terminal.plist"
    with prefs.open("rb") as f:
        doc = plistlib.load(f)
    name = name or doc.get("Default Window Settings", "Basic")
    settings = doc.get("Window Settings", {}).get(name)
    if settings is None:
        sys.exit(f"make-terminal-launchers: Terminal profile {name!r} not found")
    return dict(settings)


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(Path.home() / "Apps"))
    ap.add_argument("--profile", help="Terminal profile to copy (default: your default profile)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written")
    args = ap.parse_args(argv)
    base = profile(args.profile)
    out = Path(args.out).expanduser()
    rc = 0
    for fname, (target, title) in LAUNCHERS.items():
        doc = dict(base)
        doc.update({
            "name": title,
            "type": "Window Settings",
            "CommandString": str(target),
            "RunCommandAsShell": False,       # run the program itself: no shell, no banner
            "shellExitAction": 1,             # close the window when the picker exits
            "CustomWindowTitle": title,
            "ShowCommandKeyInTitle": False,
        })
        path = out / fname
        if args.dry_run:
            print(f"would write {path} -> {target}")
            continue
        if path.exists():
            print(f"exists, left untouched: {path}", file=sys.stderr)
            rc = 1
            continue
        out.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            plistlib.dump(doc, f)
        print(f"wrote {path}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
