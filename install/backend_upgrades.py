#!/usr/bin/env python3
"""backend_upgrades.py — find backend upgrades and run them after a yes/no question.

An UPGRADE here is a newer release of the backend itself, installed the way that backend is
installed on this machine — never a bare dependency bump. (The package-level check in
check_updates() also reports e.g. mlx-lm 0.32.0 for rapid-mlx 0.16.1, whose own metadata pins
mlx-lm<0.32: "upgrading" that inside rapid's venv would break rapid, so it is not offered.)

  backend    installed through          upgrade command
  omlx       Homebrew tap jundot/omlx   brew upgrade jundot/omlx/omlx
  moespresso Homebrew tap steadfastgaze  brew upgrade steadfastgaze/tap/moespresso@3
  llama-cpp  Homebrew                   brew upgrade llama.cpp
  litellm    pipx                       pipx upgrade litellm   (keeps pipx-injected packages, e.g. prisma)
  rapid-mlx  versioned venv (manager)   manage-backend.py --backend rapid-mlx install <latest>
  vllm-mlx   versioned venv (manager)   manage-backend.py --backend vllm-mlx install <latest>

A backend that is not installed through that route is skipped, never "upgraded" into a second copy.

Usage:
  backend_upgrades.py --check            refresh the pending list (network; the weekly job runs this)
  backend_upgrades.py --list [--json]    show pending upgrades from the last check (no network)
  backend_upgrades.py --prompt           ask yes/no for each pending upgrade and run the accepted ones
                                         (Enter = yes). Needs a terminal; prints nothing when idle.
  backend_upgrades.py --prompt --yes     run every pending upgrade without asking

A "no" is remembered for that exact version only: the next newer release is offered again.

Files:
  ~/.claude/free-agents/backend-upgrades.json   pending list + declined versions

Environment:
  LA_BACKEND_UPGRADES_STATE   state file override (tests)
  LA_BACKEND_UPGRADES=0       never prompt (csl start skips the question)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
MANAGE_BACKEND = HERE / "manage-backend.py"

# Homebrew formulae kept PINNED so a blanket `brew upgrade` (the user's own, or the daily job)
# cannot move them unchecked; their offer is the one route that moves them, and it unpins,
# upgrades and re-pins. moespresso@3: a 3.x patch rebuilt on python@3.15 must be held back,
# and 4.x is a different engine that cannot load the DeepSeek V4 package (user 2026-10-26).
PINNED_FORMULAE = ("steadfastgaze/tap/moespresso@3",)
# Newest Python litellm and MoEspresso may run on. One value with the venv managers'
# BackendManager.PYTHON_MAX: torch-era wheels lag each CPython release.
PYTHON_MAX = (3, 14)


def _minor(text: str) -> tuple[int, int] | None:
    """'Python 3.15.1' / 'python@3.15' / '3.15' -> (3, 15); None when nothing parses."""
    import re
    m = re.search(r"(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def ceiling_python() -> str:
    """A <= PYTHON_MAX interpreter, by the venv managers' own chooser (stable symlink path)."""
    sys.path.insert(0, str(HERE))
    import importlib
    importlib.import_module("managers.litellm")
    return str(importlib.import_module("managers").get_manager_instance("litellm").choose_python(None))


def formula_python_too_new(formula: str, run: "Runner") -> bool:
    """True when the formula, as Homebrew would install it now, depends on python@>PYTHON_MAX."""
    r = run(["brew", "info", "--json=v2", formula])
    try:
        deps = json.loads(r.stdout or "{}")["formulae"][0].get("dependencies", [])
    except (ValueError, KeyError, IndexError):
        return True    # unreadable is not "safe": hold the upgrade rather than guess
    pys = [v for d in deps if d.startswith("python@") and (v := _minor(d))]
    return any(v > PYTHON_MAX for v in pys)


@dataclass
class Upgrade:
    backend: str
    current: str
    latest: str
    command: list[str] = field(default_factory=list)
    via: str = ""


def state_path() -> Path:
    override = os.environ.get("LA_BACKEND_UPGRADES_STATE")
    return Path(override) if override else Path.home() / ".claude" / "free-agents" / "backend-upgrades.json"


def load_state() -> dict:
    try:
        data = json.loads(state_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(data: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(p)


Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE=os.environ.get("HOMEBREW_NO_AUTO_UPDATE", "1"))
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)


def _pypi_latest(package: str) -> str | None:
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json", timeout=15) as r:
            return json.load(r).get("info", {}).get("version") or None
    except (OSError, ValueError):
        return None


# --- detection per install route ------------------------------------------------------------
def brew_upgrades(run: Runner = _run) -> list[Upgrade]:
    """Homebrew's own verdict (handles revisions like 0.6.0_1 and tap rc versions)."""
    names = {"jundot/omlx/omlx": "omlx", "llama.cpp": "llama-cpp",
             # The @3 formula, not `moespresso`: 4.x cannot load a MoEspresso-packaged
             # DeepSeek V4 artifact, and it is the one `brew upgrade` would otherwise move.
             "steadfastgaze/tap/moespresso@3": "moespresso"}
    installed = [n for n in names if run(["brew", "list", "--versions", n.split("/")[-1]]).returncode == 0]
    if not installed:
        return []
    r = run(["brew", "outdated", "--json=v2", *installed])
    try:
        formulae = json.loads(r.stdout or "{}").get("formulae", [])
    except ValueError:
        return []
    out = []
    for f in formulae:
        full = f.get("name", "")
        key = next((k for k in names if k == full or k.split("/")[-1] == full), None)
        if key is None:
            continue
        cur = (f.get("installed_versions") or ["?"])[-1]
        if key in PINNED_FORMULAE and formula_python_too_new(key, run):
            continue   # held: this release would put the backend on Python > PYTHON_MAX
        out.append(Upgrade(names[key], cur, f.get("current_version", "?"), ["brew", "upgrade", key], "homebrew"))
    return out


def pipx_upgrades(run: Runner = _run, latest: Callable[[str], str | None] = _pypi_latest) -> list[Upgrade]:
    r = run(["pipx", "list", "--json"])
    try:
        venvs = json.loads(r.stdout or "{}").get("venvs", {})
    except ValueError:
        return []
    md = venvs.get("litellm", {}).get("metadata", {})
    cur = md.get("main_package", {}).get("package_version")
    if not cur:
        return []
    py = _minor(md.get("python_version", ""))
    if py and py > PYTHON_MAX:
        # `pipx upgrade` keeps a venv's interpreter, so it can never repair this; a reinstall on a
        # <= PYTHON_MAX Python does, keeps injected packages, and lands the newest release.
        return [Upgrade("litellm", cur, latest("litellm") or cur,
                        ["pipx", "reinstall", "--python", "<ceiling>", "litellm"], "pipx-python")]
    new = latest("litellm")
    if not new or not _newer(new, cur):
        return []
    return [Upgrade("litellm", cur, new, ["pipx", "upgrade", "litellm"], "pipx")]


def _pipx_litellm_python(run: "Runner") -> tuple[int, int] | None:
    try:
        meta = json.loads(run(["pipx", "list", "--json"]).stdout or "{}")
        return _minor(meta["venvs"]["litellm"]["metadata"].get("python_version", ""))
    except (ValueError, KeyError):
        return None


def venv_upgrades(mgrs: dict | None = None) -> list[Upgrade]:
    """rapid-mlx / vllm-mlx: the newest installed versioned venv vs the newest release."""
    if mgrs is None:
        sys.path.insert(0, str(HERE))
        import importlib
        for mod in ("managers.rapid_mlx", "managers.vllm_mlx"):
            importlib.import_module(mod)
        get_instance = importlib.import_module("managers").get_manager_instance
        mgrs = {n: get_instance(n) for n in ("rapid-mlx", "vllm-mlx")}
    out = []
    for name, mgr in mgrs.items():
        try:
            installed = mgr.get_installed_versions()
            if not installed:
                continue
            new = mgr.get_latest_version()
        except Exception:
            continue
        if _newer(new, installed[0], mgr):
            out.append(Upgrade(name, installed[0], new,
                               [sys.executable, str(MANAGE_BACKEND), "--backend", name, "install", new],
                               "versioned venv"))
    return out


def live_command(u: Upgrade) -> list[str]:
    """The argv to run NOW. A versioned-venv offer is rebuilt from this checkout's manager: the
    stored argv names the interpreter and script of whichever tree ran --check, and that tree may
    be gone (measured 2026-10-26: a deleted worktree's manage-backend.py, replayed on every launch).
    State records WHAT to upgrade; WHERE the code lives is resolved at use time."""
    if u.via == "versioned venv":
        return [sys.executable, str(MANAGE_BACKEND), "--backend", u.backend, "install", u.latest]
    if u.via == "pipx-python":
        return ["pipx", "reinstall", "--python", ceiling_python(), "litellm"]
    if u.via == "homebrew" and u.command[-1] in PINNED_FORMULAE:
        f = u.command[-1]
        return ["/bin/sh", "-c", f"brew unpin {f}; brew upgrade {f}; rc=$?; brew pin {f}; exit $rc"]
    return u.command


def _newer(a: str, b: str, mgr=None) -> bool:
    if mgr is not None and hasattr(mgr, "_version_newer"):
        try:
            return mgr._version_newer(a, b)
        except Exception:
            pass
    def key(v: str):
        import re
        return [int(x) if x.isdigit() else x for x in re.split(r"[.\-_]|(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", v)]
    try:
        return key(a) > key(b)
    except TypeError:
        return a != b


def check(run: Runner = _run, mgrs: dict | None = None) -> list[Upgrade]:
    found = brew_upgrades(run) + pipx_upgrades(run) + venv_upgrades(mgrs)
    data = load_state()
    data["pending"] = [asdict(u) for u in found]
    save_state(data)
    return found


def pending(state: dict | None = None) -> list[Upgrade]:
    data = load_state() if state is None else state
    declined = data.get("declined", {})
    out = []
    for raw in data.get("pending", []):
        try:
            u = Upgrade(**raw)
        except TypeError:
            continue
        if declined.get(u.backend) == u.latest:
            continue
        out.append(u)
    return out


def still_needed(u: Upgrade, run: Runner = _run) -> bool:
    """Cheap, local re-check at prompt time: the nightly brew/pipx job may already have done it."""
    # "Needed" means the installed version is OLDER than the offer -- not merely different. A user
    # who upgraded past the cached offer by hand (litellm 1.106.0 installed, 1.105.1 offered,
    # measured 2026-10-20) must not be asked to "upgrade" to the older release.
    if u.via == "pipx-python":
        py = _pipx_litellm_python(run)
        return bool(py and py > PYTHON_MAX)
    if u.via == "homebrew":
        r = run(["brew", "list", "--versions", u.command[-1].split("/")[-1]])
        parts = r.stdout.split()
        if u.command[-1] in PINNED_FORMULAE and formula_python_too_new(u.command[-1], run):
            return False   # the formula moved to a too-new Python since the check: hold it
        return r.returncode == 0 and len(parts) > 1 and _newer(u.latest, parts[-1])
    if u.via == "pipx":
        try:
            meta = json.loads(run(["pipx", "list", "--json"]).stdout or "{}")
            cur = meta["venvs"]["litellm"]["metadata"]["main_package"]["package_version"]
        except (ValueError, KeyError):
            return False
        return _newer(u.latest, cur)
    return True


def prompt(assume_yes: bool, ask: Callable[[str], str] = input, run_live: Callable | None = None,
           run: Runner = _run, out=sys.stdout) -> int:
    data = load_state()
    offers = pending(data)
    todo = [u for u in offers if still_needed(u, run)]
    stale = {u.backend for u in offers} - {u.backend for u in todo}
    if stale:      # already at (or past) the offered release: drop the offer, do not re-ask next launch
        data["pending"] = [p for p in data.get("pending", []) if p.get("backend") not in stale]
        save_state(data)
    if not todo:
        return 0
    run_live = run_live or (lambda cmd: subprocess.run(cmd).returncode)
    failed = 0
    print(f"⬆️  {len(todo)} backend upgrade(s) available.", file=out)
    for u in todo:
        cmd = live_command(u)
        q = f"Upgrade {u.backend} {u.current} -> {u.latest}?  runs: {' '.join(cmd)}  [Y/n] "
        answer = "y" if assume_yes else ask(q).strip().lower()
        if answer in ("", "y", "yes"):
            print(f"→ {' '.join(cmd)}", file=out)
            rc = run_live(cmd)
            if rc == 0:
                print(f"✅ {u.backend} upgraded to {u.latest}", file=out)
                data["pending"] = [p for p in data.get("pending", []) if p.get("backend") != u.backend]
            else:
                failed += 1
                print(f"❌ {u.backend} upgrade failed (exit {rc}); it stays pending", file=out)
        else:
            data.setdefault("declined", {})[u.backend] = u.latest
            print(f"skipped {u.backend} {u.latest} — not asked again until a newer release", file=out)
        save_state(data)
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    doc = __doc__ or ""
    ap = argparse.ArgumentParser(prog="backend_upgrades.py", description=doc.split("\n\n")[0],
                                 epilog=doc.split("\n\n", 1)[-1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="refresh the pending list (network)")
    mode.add_argument("--list", action="store_true", help="show pending upgrades (no network)")
    mode.add_argument("--prompt", action="store_true", help="ask and run accepted upgrades")
    ap.add_argument("--yes", action="store_true", help="with --prompt: run every pending upgrade")
    ap.add_argument("--json", action="store_true", help="with --list/--check: JSON output")
    args = ap.parse_args(argv)
    if args.check or args.list:
        items = check() if args.check else pending()
        if args.json:
            print(json.dumps([asdict(u) for u in items], indent=2))
        else:
            for u in items:
                print(f"{u.backend}\t{u.current} -> {u.latest}\t{' '.join(live_command(u))}")
            if not items:
                print("no backend upgrades pending")
        return 0
    if os.environ.get("LA_BACKEND_UPGRADES") == "0":
        return 0
    if not args.yes and not sys.stdin.isatty():
        print("backend_upgrades.py: --prompt needs a terminal (or pass --yes)", file=sys.stderr)
        return 2
    return prompt(args.yes)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
