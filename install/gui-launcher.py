#!/usr/bin/env python3
"""gui-launcher.py — generate, check, repair or remove the thin Free Agents launcher.

macOS: compiles ~/Applications/Free Agents.app ON THIS MAC from install/gui-launcher/ with the
system's own /usr/bin/osacompile (no Xcode needed), adds the bundle identity, and re-signs it
ad-hoc (/usr/bin/codesign --sign -) so the edited bundle is valid code. Ad-hoc signing is code
validity only: it is not a Developer ID, not notarization, and not a stable TCC identity.
The app contains only the applet, its icon, Info.plist and Contents/Resources/locate.sh; it
finds the installed plugin at click time, so plugin upgrades need no regeneration. Linux: the
Desktop Entry equivalent is planned (plan Task 14) and not generated yet.

Usage: gui-launcher.py {generate,check,repair,remove} [--dest DIR] [--dry-run]
                       [--plugin-override PATH] [--help]
  generate   create the app, or report "up to date" when an owned app of this contract exists
  check      verify the app (ownership, contract, signature) and run locate.sh --check
  repair     regenerate an owned app unconditionally (a foreign app is never touched)
  remove     delete an owned app; keeps the config, the GUI venv, the plugin and its settings
  --dest DIR             where the app goes (default ~/Applications)
  --dry-run              print what would happen, write nothing
  --plugin-override PATH record FREE_AGENTS_PLUGIN=PATH in the config (explicit checkout pin)

Environment:
  XDG_CONFIG_HOME   config dir base (default ~/.config); config = free-agents/gui-launcher.conf
  LA_GUI_VENV       GUI venv recorded in the config (default ~/.local/share/free-agents/gui-venv)
"""
from __future__ import annotations

import argparse
import datetime
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "install/gui-launcher"
APP_NAME = "Free Agents"
BUNDLE_ID = "io.github.haiggoh.free-agents.launcher"
CONTRACT = 1          # bump when locate.sh's interface or the applet changes incompatibly
ICON = ROOT / "config/gui/FreeAgents.icns"
CONF_KEYS = ("FREE_AGENTS_PLUGIN", "PLUGIN_CACHE", "PLUGIN_HINT", "GUI_VENV", "CHANNEL", "CONTRACT")
KEEP_KEYS = ("FREE_AGENTS_PLUGIN", "PLUGIN_CACHE")   # user pins survive regeneration


def die(msg: str, code: int = 1) -> int:
    print(f"gui-launcher: {msg}", file=sys.stderr)
    return code


def conf_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "free-agents/gui-launcher.conf"


def read_conf(path: Path) -> dict:
    out = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k in CONF_KEYS and len(v) >= 2 and v[0] == v[-1] == "'":
                out[k] = v[1:-1]
    return out


def channel() -> str:
    s = str(ROOT)
    if "/.claude/plugins/cache/" in s:
        return "marketplace"
    if "/Cellar/" in s or "/homebrew/" in s.lower() or "/linuxbrew/" in s:
        return "brew"
    return "checkout"


def gui_venv() -> str:
    v = os.environ.get("LA_GUI_VENV")
    if v and not Path(v).is_dir():
        raise SystemExit(die(f"LA_GUI_VENV={v} is set but is not a directory", 2))
    return v or str(Path.home() / ".local/share/free-agents/gui-venv")


def new_conf(old: dict, override: str | None) -> dict:
    conf = {k: old[k] for k in KEEP_KEYS if old.get(k)}
    if override:
        conf["FREE_AGENTS_PLUGIN"] = override
    conf.update(PLUGIN_HINT=str(ROOT), GUI_VENV=gui_venv(), CHANNEL=channel(), CONTRACT=str(CONTRACT))
    return conf


def write_conf(path: Path, conf: dict) -> None:
    for k, v in conf.items():
        if "'" in v or "\n" in v:
            raise SystemExit(die(f"{k}={v!r}: a quote or newline cannot be stored in the config", 2))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("# written by install/gui-launcher.py — KEY='value', parsed (never sourced) "
                   "by locate.sh\n" + "".join(f"{k}='{conf[k]}'\n" for k in CONF_KEYS if k in conf))
    tmp.replace(path)


def ownership(app: Path) -> tuple[str, dict]:
    """("absent"|"owned"|"foreign", plist)."""
    info = app / "Contents/Info.plist"
    if not app.exists():
        return "absent", {}
    try:
        p = plistlib.loads(info.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return "foreign", {}
    return ("owned" if p.get("CFBundleIdentifier") == BUNDLE_ID else "foreign"), p


def signature_ok(app: Path) -> tuple[bool, str]:
    r = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "--deep", str(app)],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def build(target: Path) -> None:
    """Compile the applet into `target` (which must not exist) and make it a valid owned bundle."""
    r = subprocess.run(["/usr/bin/osacompile", "-o", str(target), str(SRC / "FreeAgents.applescript")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"osacompile failed: {r.stderr.strip()}")
    res = target / "Contents/Resources"
    shutil.copy2(SRC / "locate.sh", res / "locate.sh")
    (res / "locate.sh").chmod(0o755)
    if ICON.is_file():
        shutil.copy2(ICON, res / "applet.icns")
    info = target / "Contents/Info.plist"
    p = plistlib.loads(info.read_bytes())
    # The applet scripts nothing and reads no personal data: drop osacompile's blanket
    # permission strings so no TCC prompt can cite a reason that does not apply.
    for k in [k for k in p if k.startswith("NS") and k.endswith("UsageDescription")]:
        del p[k]
    p.update(CFBundleIdentifier=BUNDLE_ID, CFBundleName=APP_NAME, CFBundleDisplayName=APP_NAME,
             CFBundleShortVersionString=str(CONTRACT), FreeAgentsLauncherContract=CONTRACT,
             FreeAgentsGeneratedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             FreeAgentsSourceRoot=str(ROOT))
    info.write_bytes(plistlib.dumps(p))
    r = subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(target)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"codesign failed: {r.stderr.strip()}")
    ok, detail = signature_ok(target)
    if not ok:
        raise RuntimeError(f"the generated bundle does not verify: {detail}")


def cmd_generate(args, force: bool) -> int:
    if sys.platform != "darwin":
        return die("only macOS is generated so far; the Linux Desktop Entry is plan Task 14. "
                   "Run bin/free-agents-gui directly meanwhile.", 2)
    if "'" in str(ROOT):
        return die(f"the plugin path {ROOT} contains a quote, which the launcher config cannot hold", 2)
    app = Path(args.dest).expanduser() / f"{APP_NAME}.app"
    state, p = ownership(app)
    if state == "foreign":
        return die(f"{app} exists and is not ours (bundle id {p.get('CFBundleIdentifier', '?')!r}); "
                   "move it away or choose --dest — it is never overwritten")
    cpath = conf_path()
    conf = new_conf(read_conf(cpath), args.plugin_override)
    up_to_date = (state == "owned" and p.get("FreeAgentsLauncherContract") == CONTRACT
                  and signature_ok(app)[0] and not force)
    if args.dry_run:
        print(f"would {'keep (up to date)' if up_to_date else 'create' if state == 'absent' else 'regenerate'}: {app}")
        print(f"would write config {cpath}: " + ", ".join(f"{k}={v}" for k, v in conf.items()))
        return 0
    if up_to_date:
        write_conf(cpath, conf)
        print(f"up to date: {app} (contract {CONTRACT})")
        return 0
    app.parent.mkdir(parents=True, exist_ok=True)
    # Must END in .app: osacompile picks the output type from the extension (otherwise it writes
    # a compiled .scpt file, not a bundle).
    tmp = app.with_name(f".{APP_NAME}.tmp-{os.getpid()}.app")
    old = app.with_name(f".{APP_NAME}.old-{os.getpid()}.app")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        build(tmp)             # a failed build leaves the working app untouched
    except (RuntimeError, OSError) as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        return die(str(exc))
    if state == "owned":
        app.rename(old)
    tmp.rename(app)
    shutil.rmtree(old, ignore_errors=True)
    write_conf(cpath, conf)
    print(f"{'created' if state == 'absent' else 'regenerated'}: {app}")
    print(f"config: {cpath}")
    return 0


def cmd_check(args) -> int:
    app = Path(args.dest).expanduser() / f"{APP_NAME}.app"
    state, p = ownership(app)
    if state != "owned":
        return die(f"{app}: {state}" + (" — run: gui-launcher.py generate" if state == "absent" else ""))
    problems = []
    if p.get("FreeAgentsLauncherContract") != CONTRACT:
        problems.append(f"contract {p.get('FreeAgentsLauncherContract')} != {CONTRACT} (run repair)")
    ok, detail = signature_ok(app)
    if not ok:
        problems.append(f"signature: {detail} (run repair)")
    r = subprocess.run(["/bin/sh", str(app / "Contents/Resources/locate.sh"), "--check"],
                       capture_output=True, text=True)
    print(f"app: {app} (contract {p.get('FreeAgentsLauncherContract')}, generated "
          f"{p.get('FreeAgentsGeneratedAt', '?')}, from {p.get('FreeAgentsSourceRoot', '?')})")
    print(r.stdout.strip())
    if r.returncode != 0:
        problems.append(r.stderr.strip())
    for prob in problems:
        print(f"problem: {prob}", file=sys.stderr)
    return 1 if problems else 0


def cmd_remove(args) -> int:
    app = Path(args.dest).expanduser() / f"{APP_NAME}.app"
    state, _ = ownership(app)
    if state == "absent":
        print(f"nothing to remove: {app}")
        return 0
    if state == "foreign":
        return die(f"{app} is not ours; not removed")
    if args.dry_run:
        print(f"would remove {app} (config, GUI venv, plugin and settings are kept)")
        return 0
    shutil.rmtree(app)
    print(f"removed {app} (kept: {conf_path()}, the GUI venv, the plugin and its settings)")
    return 0


def main(argv: list[str]) -> int:
    doc = __doc__ or ""
    ap = argparse.ArgumentParser(prog="gui-launcher.py", description=doc.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=doc.split("\n\n", 1)[-1])
    ap.add_argument("command", choices=("generate", "check", "repair", "remove"))
    ap.add_argument("--dest", default=str(Path.home() / "Applications"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--plugin-override", default=None)
    args = ap.parse_args(argv)
    if args.plugin_override and not (Path(args.plugin_override) / ".claude-plugin/plugin.json").is_file():
        return die(f"--plugin-override {args.plugin_override}: not a Free Agents plugin dir", 2)
    if args.command in ("generate", "repair"):
        return cmd_generate(args, force=args.command == "repair")
    if args.command == "check":
        return cmd_check(args)
    return cmd_remove(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
