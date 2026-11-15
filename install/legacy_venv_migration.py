#!/usr/bin/env python3
"""Legacy-venv migration offers (0.58.0) — the offer SOURCE for the launch prompt.

Detection only; the prompt loop lives in backend_upgrades.py. One offer per managed
legacy venv (~/.venvs/<prefix>-<ver>, prefixes of the registered managers with
SUPPORTS_VENV_REMOVE) that has NO same-name dir in the new XDG root. The offer's
command is the backend manager's own `repair <ver>`, which rebuilds the venv in the
new root make-before-break and re-pins a live pin (0.58.0 Task 3). Unmanaged
~/.venvs/* entries are never listed, offered or touched.

Usage:
  legacy_venv_migration.py --list [--json]   show pending migration offers (no network)
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import managers  # noqa: E402
import managers.litellm  # noqa: E402,F401  (registers the venv managers)
import managers.rapid_mlx  # noqa: E402,F401
import managers.vllm_mlx  # noqa: E402,F401
from managers.base import backends_root, legacy_venvs_root  # noqa: E402

MANAGE_BACKEND = HERE / "manage-backend.py"


def managed_prefixes() -> list[str]:
    """VENV_PREFIX of every registered manager that owns versioned venvs."""
    out = set()
    for _name, cls in managers.iter_managers():
        if getattr(cls, "SUPPORTS_VENV_REMOVE", False):
            out.add(getattr(cls, "VENV_PREFIX", "") or cls.BACKEND_NAME)
    return sorted(out)


def offers(home: Path | None = None) -> list:
    """One migrate-offer per legacy-only managed venv, sorted by (backend, version)."""
    from backend_upgrades import Upgrade  # lazy: backend_upgrades imports this module
    legacy = legacy_venvs_root(home)
    new = backends_root(home)
    out = []
    if not legacy.is_dir():
        return out
    for prefix in managed_prefixes():
        for d in sorted(legacy.glob(f"{prefix}-*")):
            if not d.is_dir() or d.is_symlink():
                continue
            ver = d.name.removeprefix(f"{prefix}-")
            if not ver[:1].isdigit():
                continue  # managed names only: <prefix>-<X.Y.Z>
            if ".repair-" in d.name:
                continue  # stale repair-aside dir (killed repair), never a version (0.58.1)
            if (new / d.name).is_dir():
                continue  # present in both roots: the new root wins, no offer
            out.append(Upgrade(backend=prefix, current=ver, latest=ver, via="migrate",
                               command=[sys.executable, str(MANAGE_BACKEND),
                                        "--backend", prefix, "repair", ver]))
    return sorted(out, key=lambda u: (u.backend, u.current))


def main(argv: list[str] | None = None) -> int:
    doc = __doc__ or ""
    ap = argparse.ArgumentParser(prog="legacy_venv_migration.py",
                                 description=doc.split("\n\n")[0],
                                 epilog=doc.split("\n\n", 1)[-1] if "\n\n" in doc else None,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="list pending legacy-venv migration offers (no network)")
    ap.add_argument("--json", action="store_true", help="with --list: JSON output")
    args = ap.parse_args(argv)
    if not args.list:
        ap.print_help()
        return 0 if argv is None or not argv else 2
    items = offers()
    if args.json:
        print(json.dumps([asdict(u) for u in items], indent=2))
    else:
        for u in items:
            print(f"{u.backend}\t{u.current}\tmigrate\t{' '.join(u.command)}")
        if not items:
            print("no legacy venvs to migrate")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
