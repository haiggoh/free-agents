#!/usr/bin/env python3
"""durability.py — keep backend venvs alive across Homebrew Python PATCH upgrades.

A venv whose pyvenv.cfg `home` / bin/python3.X link names a patch keg
(<prefix>/Cellar/python@3.14/3.14.8_2/…) dies when `brew upgrade` installs the next patch and
deletes that keg (measured 2026-10-30: rapid-mlx-0.16.1). One whose base is
<prefix>/opt/python@3.14/bin survives, because Homebrew repoints opt/ at the new keg (measured:
rapid-mlx-0.16.9 survived 3.14.8_2 -> 3.14.10). How venvs end up keg-based could not be reproduced,
so the guard is OUTCOME-based: after creating a venv, normalize it, then RUN it.

Usage:
  durability.py check <venv>       print durable | pinned-keg (exit 0)
  durability.py normalize <venv>   rewrite a keg-based base onto opt/, run it; print durable |
                                   normalized | pinned-keg (exit 1 for pinned-keg)

Environment:
  HOMEBREW_PREFIX   the Homebrew prefix to recognise (default: `brew --prefix`, then
                    /opt/homebrew, /usr/local, /home/linuxbrew/.linuxbrew where present)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_FALLBACK_PREFIXES = ("/opt/homebrew", "/usr/local", "/home/linuxbrew/.linuxbrew")


def homebrew_prefixes() -> list[Path]:
    """Every Homebrew prefix to recognise. An explicit HOMEBREW_PREFIX is the only one used."""
    explicit = os.environ.get("HOMEBREW_PREFIX")
    if explicit:
        return [Path(explicit)]
    found: list[Path] = []
    brew = shutil.which("brew")
    if brew:
        try:
            out = subprocess.run([brew, "--prefix"], capture_output=True, text=True, timeout=15)
            if out.returncode == 0 and out.stdout.strip():
                found.append(Path(out.stdout.strip()))
        except (OSError, subprocess.SubprocessError):
            pass
    found += [Path(p) for p in _FALLBACK_PREFIXES if Path(p, "Cellar").is_dir()]
    return list(dict.fromkeys(found))


def _keg(path: str) -> tuple[Path, str] | None:
    """(prefix, "X.Y") when `path` lies inside a python@X.Y patch keg, else None."""
    for prefix in homebrew_prefixes():
        m = re.match(re.escape(str(prefix)) + r"/Cellar/python@(\d+\.\d+)/[^/]+/", path)
        if m:
            return prefix, m.group(1)
    return None


def _link_chain(path: Path, limit: int = 12) -> list[str]:
    """Every hop of a symlink chain, unresolved: Homebrew's <prefix>/bin/python3.14 names its keg
    only in the MIDDLE hop (-> ../Cellar/python@3.14/<patch>/bin/python3.14), so neither the input
    nor the final realpath alone reveals it."""
    hops, current = [], str(path)
    for _ in range(limit):
        hops.append(current)
        if not os.path.islink(current):
            break
        current = os.path.normpath(os.path.join(os.path.dirname(current), os.readlink(current)))
    return hops


def durable_python(path: Path) -> Path:
    """The patch-stable spelling of a Homebrew interpreter (<prefix>/opt/python@X.Y/bin/pythonX.Y),
    or `path` unchanged when it is not a Homebrew python or the opt/ link is missing."""
    for candidate in _link_chain(path):
        keg = _keg(candidate)
        if keg:
            prefix, mm = keg
            stable = prefix / "opt" / f"python@{mm}" / "bin" / f"python{mm}"
            if stable.exists():
                return stable
    return path


def _bin_links(target: Path) -> list[Path]:
    return [p for p in sorted((target / "bin").glob("python*")) if p.is_symlink()]


def venv_durability(target: Path) -> str:
    """'pinned-keg' when the venv's base names a patch keg (it dies at the next patch upgrade)."""
    cfg = target / "pyvenv.cfg"
    home = ""
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("home ="):
                home = line.split("=", 1)[1].strip() + "/"
    links = [os.readlink(p) for p in _bin_links(target)]
    return "pinned-keg" if any(_keg(x) for x in [home, *links] if x) else "durable"


def normalize_venv_base(target: Path, *, runner=subprocess.run) -> str:
    """Rewrite a keg-based venv onto <prefix>/opt/python@X.Y and RUN it.

    Returns 'durable' (nothing to do; nothing written), 'normalized', or 'pinned-keg' (could not
    be moved — the original pyvenv.cfg and links are restored byte-for-byte)."""
    if venv_durability(target) == "durable":
        return "durable"
    cfg = target / "pyvenv.cfg"
    original_cfg = cfg.read_bytes()
    original_links = {p: os.readlink(p) for p in _bin_links(target)}
    keg = next((k for k in (_keg(x) for x in original_links.values()) if k), None)
    if keg is None:
        for line in original_cfg.decode("utf-8", "replace").splitlines():
            if line.startswith("home ="):
                keg = _keg(line.split("=", 1)[1].strip() + "/")
    if keg is None:
        return "pinned-keg"
    prefix, mm = keg
    opt_bin = prefix / "opt" / f"python@{mm}" / "bin"
    opt_exe = opt_bin / f"python{mm}"
    if not opt_exe.exists():
        return "pinned-keg"
    try:
        lines = []
        for line in original_cfg.decode("utf-8").splitlines():
            if line.startswith("home ="):
                line = f"home = {opt_bin}"
            elif line.startswith("executable ="):
                line = f"executable = {opt_exe}"
            lines.append(line)
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for link, dest in original_links.items():
            if _keg(dest):
                link.unlink()
                link.symlink_to(opt_exe)
        probe = runner([str(target / "bin" / "python"), "-c", "import encodings"],
                       capture_output=True, text=True, timeout=30)
        if probe.returncode == 0:
            return "normalized"
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        pass
    cfg.write_bytes(original_cfg)
    for link, dest in original_links.items():
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(dest)
    return "pinned-keg"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Keep backend venvs alive across Homebrew Python patch upgrades.",
        epilog="Environment: HOMEBREW_PREFIX overrides the Homebrew prefix to recognise.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, text in (("check", "print durable | pinned-keg"),
                       ("normalize", "rewrite a keg-based venv onto opt/ and run it")):
        p = sub.add_parser(name, help=text)
        p.add_argument("venv", type=Path)
    args = parser.parse_args(argv)
    if not (args.venv / "pyvenv.cfg").is_file():
        print(f"durability.py: not a venv (no pyvenv.cfg): {args.venv}", file=sys.stderr)
        return 2
    result = venv_durability(args.venv) if args.cmd == "check" else normalize_venv_base(args.venv)
    print(result)
    return 1 if (args.cmd == "normalize" and result == "pinned-keg") else 0


if __name__ == "__main__":
    raise SystemExit(main())
