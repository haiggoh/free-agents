#!/usr/bin/env python3
"""Unified Backend Manager CLI.

Manage versioned backend runtimes (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm)
through a common interface.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from pathlib import Path

# Add install directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Network shim (IPv4 forcing + Keychain truststore) — the same one the downloader and
# remote-session.sh use. Release discovery fetches PyPI with urllib, and on home wifi IPv6 to
# the PyPI CDN black-holes: 8 AAAA records x a 15 s timeout outlasted the picker's 60 s cap, so
# "Install a release" spun, then claimed "no releases found" (2026-10-29). Applied here because
# every caller passes through this script; exported so its pip/venv children get it too.
NETWORK_SHIM = Path(__file__).resolve().parent / "hf-ipv4"
if (NETWORK_SHIM / "sitecustomize.py").is_file():
    _loaded = getattr(sys.modules.get("sitecustomize"), "__file__", "") or ""
    if Path(_loaded).resolve().parent != NETWORK_SHIM:
        runpy.run_path(str(NETWORK_SHIM / "sitecustomize.py"))
    _pp = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p and Path(p) != NETWORK_SHIM]
    os.environ["PYTHONPATH"] = os.pathsep.join([str(NETWORK_SHIM), *_pp])

# Import managers to trigger registration via @register_manager decorator
try:
    import managers.rapid_mlx  # noqa: F401
except ImportError:
    pass
try:
    import managers.vllm_mlx  # noqa: F401
except ImportError:
    pass
try:
    import managers.omlx  # noqa: F401
except ImportError:
    pass
try:
    import managers.llama_cpp  # noqa: F401
except ImportError:
    pass
try:
    import managers.moespresso  # noqa: F401
except ImportError:
    pass
try:
    import managers.litellm  # noqa: F401
except ImportError:
    pass
# CUDA backends (0.49.0 Task 14): registered managers like the Mac ones, venvs under
# <LA_USER_STATE_DIR>/cuda-backends/<name>-<version>; no CUDA hardware needed to register.
try:
    import managers._cuda  # noqa: F401
except ImportError:
    pass

from managers import (
    BackendManager,
    ManagerError,
    get_manager,
    get_manager_instance,
    list_managers,
)

# The repository this script ships in — the documented "default: script repository" of --repo
# (0.41.1: it was documented but never wired, so pin promotion was impossible without --repo
# and the picker's install+promote silently left pin_update "pending"). For an installed
# plugin copy this resolves to the derived plugin cache, which is NOT a git checkout;
# tracked_repo_clean refuses that with the remedy instead of a generic git failure.
DEFAULT_REPO = Path(__file__).resolve().parents[1]


def _effective_version(mgr, version):
    """The version this manager's route would really install (identity unless it overrides)."""
    fn = getattr(mgr, "effective_version", None)
    return fn(version) if callable(fn) else version


def _resolve_repo(args: argparse.Namespace) -> Path:
    """--repo when given, else the repository this script ships in."""
    return Path(args.repo) if getattr(args, "repo", None) else DEFAULT_REPO


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available backends: {', '.join(list_managers())}

Examples:
  manage-backend.py --backend rapid-mlx list
  manage-backend.py --backend vllm-mlx releases --limit 10
  manage-backend.py --backend omlx install 0.7.0
  manage-backend.py --backend rapid-mlx check-updates
  manage-backend.py upgrade            ask yes/no for each pending backend upgrade, run the accepted ones
  manage-backend.py --backend rapid-mlx promote 0.16.1
  manage-backend.py --backend rapid-mlx smoke 0.16.1
  manage-backend.py --backend rapid-mlx snapshot 0.16.1
  manage-backend.py --backend rapid-mlx remove 0.16.1
  manage-backend.py --backend litellm install 1.60.0
        """.strip(),
    )
    parser.add_argument("--home", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="print the plan without executing")
    # Required for every command except `upgrade`, which walks all backends (enforced in main()).
    parser.add_argument("--backend", choices=list_managers(), help="backend to manage (not needed for `upgrade`)")
    parser.add_argument("--json", action="store_true", help="output JSON instead of text")

    sub = parser.add_subparsers(dest="command", required=True, help="Command to run")

    # list command
    sub.add_parser("list", help="list registered backends")
    sub.add_parser("installed", help="list installed versions of --backend (complete/incomplete)")

    # releases command
    rel_p = sub.add_parser("releases", help="list available releases")
    rel_p.add_argument("--pre", action="store_true", help="include prereleases")
    rel_p.add_argument("--limit", type=int, default=20)

    # install command
    inst_p = sub.add_parser("install", help="install a version")
    inst_p.add_argument("version", nargs="?", help="version to install (interactive if omitted)")
    inst_p.add_argument("--pre", action="store_true", help="offer prereleases in picker")
    inst_p.add_argument("--python", help="base Python executable")
    inst_p.add_argument("--refresh-deps", action="store_true", help="ignore existing lock file")
    inst_p.add_argument("--source", choices=["pypi", "github"], default="pypi", help="install source (vllm-mlx)")
    inst_p.add_argument("--skip-pin-update", action="store_true", help="install/validate without promoting repository/private active pins (rapid-mlx)")
    inst_p.add_argument("--repo", type=Path, help="local-agents repository (default: script repository)")

    # validate command
    val_p = sub.add_parser("validate", help="validate an installed version")
    val_p.add_argument("version")

    # check-updates command (alias: updates)
    chk_p = sub.add_parser("check-updates", help="check for package updates", aliases=["updates"])
    upg_p = sub.add_parser("upgrade", help="ask yes/no for each pending backend upgrade and run the accepted ones")
    upg_p.add_argument("--check", action="store_true", help="refresh the pending list first (network)")
    upg_p.add_argument("--yes", action="store_true", help="run every pending upgrade without asking")

    # promote command (for backends that support pin promotion)
    prom_p = sub.add_parser("promote", help="promote pins to a version")
    prom_p.add_argument("version")
    prom_p.add_argument("--repo", type=Path, help="local-agents repository (default: script repository)")

    # remove command
    rem_p = sub.add_parser("remove", help="remove an installed version")
    rem_p.add_argument("version")
    rem_p.add_argument("--yes", action="store_true", help="skip confirmation")
    rem_p.add_argument("--repo", type=Path, help="local-agents repository (default: script repository)")

    # smoke command (validate package and CLI surfaces without starting a server)
    smoke_p = sub.add_parser("smoke", help="validate package and CLI surfaces without starting a server")
    smoke_p.add_argument("version")

    # snapshot command (save an exact recreation receipt)
    snap_p = sub.add_parser("snapshot", help="save an exact recreation receipt")
    snap_p.add_argument("version")

    # health / repair (0.49.0): judge each installed version by RUNNING it; rebuild in place
    health_p = sub.add_parser("health", help="report whether each installed version can run (exit 1 if any is broken)")
    health_p.add_argument("--all", action="store_true", help="every venv backend, not only --backend")
    repair_p = sub.add_parser("repair", help="rebuild a broken or keg-fragile version in place (the original is kept until the rebuild runs)")
    repair_p.add_argument("version")

    # inspect command (capture CLI/package evidence)
    inspect_p = sub.add_parser("inspect", help="capture CLI/package evidence")
    inspect_p.add_argument("version")
    inspect_p.add_argument("--output", type=Path)

    # info command
    info_p = sub.add_parser("info", help="show detailed info about a version")
    info_p.add_argument("version")

    # launchd command
    launchd_p = sub.add_parser("launchd", help="manage launchd for automatic update checks")
    launchd_sub = launchd_p.add_subparsers(dest="launchd_action", required=True)
    launchd_sub.add_parser("install", help="install the stable launcher + launchd plist for weekly "
                                          "update checks (FA_LAUNCHD_NO_LOAD=1 skips launchctl, for tests)")
    launchd_sub.add_parser("uninstall", help="uninstall launchd plist")
    launchd_sub.add_parser("status", help="show launchd status")
    launchd_sub.add_parser("run-once", help="run update check once manually")

    return parser


def cmd_list(args: argparse.Namespace) -> int:
    """List all registered backends."""
    managers = list_managers()
    if not managers:
        print("No backends registered")
        return 0
    print("Registered backends:")
    for name in managers:
        mgr_class = get_manager(name)
        print(f"  {name:15} {mgr_class.PACKAGE_NAME}")
    return 0


def _require_backend(args: argparse.Namespace) -> BackendManager:
    """Get manager instance for the specified backend."""
    if not args.backend:
        print("ERROR: --backend is required", file=sys.stderr)
        sys.exit(2)
    mgr = get_manager_instance(args.backend, args.home)
    if mgr is None:
        print(f"ERROR: Unknown backend: {args.backend}", file=sys.stderr)
        sys.exit(2)
    if hasattr(mgr, "_repo"):
        mgr._repo = _resolve_repo(args)     # pin promotion needs the repo even without --repo
    return mgr


def cmd_releases(args: argparse.Namespace) -> int:
    """List available releases for a backend."""
    mgr = _require_backend(args)
    try:
        versions = mgr.get_available_versions(include_prereleases=args.pre)
        for version in versions[:args.limit]:
            print(version)
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_installed(args: argparse.Namespace) -> int:
    """List installed environments for a backend."""
    mgr = _require_backend(args)
    try:
        entries = mgr.installed_versions()
        if entries:
            for version, path, status in entries:
                print(f"{version:14} {status:10} {path}")
        else:
            print(f"No versioned {mgr.BACKEND_NAME} environments found.")
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Install a version."""
    mgr = _require_backend(args)
    try:
        version = args.version
        if version is None:
            # Interactive selection
            available = mgr.get_available_versions(include_prereleases=args.pre)
            installed_set = {v for v, _, _ in mgr.installed_versions()}
            print(f"Available {mgr.BACKEND_NAME} releases:")
            for idx, ver in enumerate(available[:30], 1):
                marker = " (installed)" if ver in installed_set else ""
                print(f"  {idx:2}. {ver:14}{marker}")
            if not sys.stdin.isatty():
                print("ERROR: interactive selection needs a TTY; pass a version explicitly", file=sys.stderr)
                return 2
            raw = input("Choose version number: ").strip()
            if not raw.isdigit() or not 1 <= int(raw) <= len(available):
                print("ERROR: invalid release selection", file=sys.stderr)
                return 2
            version = available[int(raw) - 1]

        mgr.require_version(version)

        if args.dry_run:
            lock = mgr.locked_requirements(version) if not args.refresh_deps else None
            print(json.dumps({
                "result": "dry_run",
                "version": _effective_version(mgr, version),
                "target": str(mgr.target_for(version)),
                "installation_mode": "locked_recreation" if lock else "fresh_resolution",
            }, indent=2))
            return 0

        # Pass skip_pin_update for rapid-mlx
        install_kwargs = {
            "version": version,
            "python": args.python,
            "refresh_deps": args.refresh_deps,
            "dry_run": False,
        }
        if hasattr(mgr, 'install_version') and 'skip_pin_update' in mgr.install_version.__code__.co_varnames:
            install_kwargs["skip_pin_update"] = getattr(args, 'skip_pin_update', False)
        if hasattr(mgr, 'install_version') and 'repo' in mgr.install_version.__code__.co_varnames:
            install_kwargs["repo"] = _resolve_repo(args)

        info = mgr.install_version(**install_kwargs)
        print(json.dumps({
            "name": info.name,
            "version": info.version,
            "path": info.path,
            "status": info.status,
            "metadata": info.metadata,
        }, indent=2))
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate an installed environment."""
    mgr = _require_backend(args)
    try:
        info = mgr.validate_environment(args.version)
        print(json.dumps({
            "name": info.name,
            "version": info.version,
            "path": info.path,
            "status": info.status,
            "metadata": info.metadata,
        }, indent=2))
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_updates(args: argparse.Namespace) -> int:
    """Check for available updates."""
    mgr = _require_backend(args)
    try:
        updates = mgr.check_updates()
        if updates:
            print(f"Updates available for {mgr.BACKEND_NAME}:")
            for package, current, latest in updates:
                print(f"  {package}: {current} -> {latest}")
        else:
            print("All packages up to date")
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """Show backend info."""
    mgr = _require_backend(args)
    try:
        if args.version:
            version = args.version
        else:
            # Find latest installed
            entries = mgr.installed_versions()
            if not entries:
                print(f"No {mgr.BACKEND_NAME} environments installed")
                return 0
            version = entries[0][0]

        info = mgr.validate_environment(version)
        print(json.dumps({
            "name": info.name,
            "version": info.version,
            "path": info.path,
            "status": info.status,
            "metadata": info.metadata,
        }, indent=2))
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    """Promote pins to a version."""
    mgr = _require_backend(args)
    try:
        if hasattr(mgr, 'promote_pins'):
            # For rapid-mlx, we need to set the repo on the manager
            repo = getattr(args, 'repo', None)
            if repo is not None and hasattr(mgr, '_repo'):
                mgr._repo = repo
            result = mgr.promote_pins(args.version, dry_run=args.dry_run)
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"ERROR: promote not supported for {mgr.BACKEND_NAME}", file=sys.stderr)
            return 2
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    """Remove an installed version."""
    mgr = _require_backend(args)
    try:
        if hasattr(mgr, 'remove_version'):
            repo = getattr(args, 'repo', None)
            mgr.remove_version(args.version, yes=args.yes, repo=repo, dry_run=args.dry_run)
            action = "would remove" if args.dry_run else "removed"
            print(f"{action} {mgr.target_for(args.version)}")
        else:
            print(f"ERROR: remove not supported for {mgr.BACKEND_NAME}", file=sys.stderr)
            return 2
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """Smoke test an environment."""
    mgr = _require_backend(args)
    try:
        if hasattr(mgr, 'smoke_environment'):
            if args.dry_run:
                print(json.dumps({"result": "dry_run", "would_smoke": args.version}, indent=2))
            else:
                result = mgr.smoke_environment(args.version)
                print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"ERROR: smoke not supported for {mgr.BACKEND_NAME}", file=sys.stderr)
            return 2
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Save a recreation receipt."""
    mgr = _require_backend(args)
    try:
        if hasattr(mgr, 'snapshot_environment'):
            path = mgr.snapshot_environment(args.version, dry_run=args.dry_run)
            print(path)
        else:
            print(f"ERROR: snapshot not supported for {mgr.BACKEND_NAME}", file=sys.stderr)
            return 2
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def _venv_managers() -> list:
    return [get_manager_instance(name) for name in list_managers()
            if getattr(get_manager(name), "SUPPORTS_VENV_REMOVE", False)]


def cmd_health(args: argparse.Namespace) -> int:
    """Report every installed version's real health; exit 1 when any is broken."""
    mgrs = _venv_managers() if args.all else [_require_backend(args)]
    rows = []
    for mgr in mgrs:
        if not getattr(mgr, "SUPPORTS_VENV_REMOVE", False):
            print(f"ERROR: health not supported for {mgr.BACKEND_NAME} (not a managed venv backend)", file=sys.stderr)
            return 2
        rows += mgr.health_report()
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        if not rows:
            print("no managed venvs installed")
        here = Path(__file__).resolve()
        for r in rows:
            pin = "  (pinned)" if r.get("pinned") else ""
            if r.get("owner") == "pipx":
                mark = "✅" if r["state"] == "complete" else "❌"
                print(f"{mark} {r['backend']} {r['version']}: {r['state']} — maintained by pipx "
                      f"(remote sessions run this one; the nightly upgrade job updates it)")
                continue
            if r.get("leftover"):
                print(f"🧹 {r['backend']} {r['version']}: leftover manager venv ({r['state']}), unused — "
                      f"remote sessions run the pipx install")
                print(f"   remove:  python3 \"{here}\" --backend {r['backend']} remove {r['version']} --yes")
                continue
            if r["state"] == "complete" and r["durability"] != "pinned-keg":
                print(f"✅ {r['backend']} {r['version']}: runs{pin}")
            elif r["state"] == "complete":
                print(f"⚠️  {r['backend']} {r['version']}: runs, but is built on a Homebrew patch keg and "
                      f"will break at the next Python patch upgrade{pin}")
                print(f"   re-base it:  python3 \"{here}\" --backend {r['backend']} repair {r['version']}")
            else:
                print(f"❌ {r['backend']} {r['version']}: {r['state']} — {r['reason']}{pin}")
                print(f"   repair:  python3 \"{here}\" --backend {r['backend']} repair {r['version']}")
    return 1 if any(r["state"] == "broken" and not r.get("leftover") for r in rows) else 0


def cmd_repair(args: argparse.Namespace) -> int:
    """Rebuild one version in place."""
    mgr = _require_backend(args)
    try:
        result = mgr.repair_version(args.version, dry_run=args.dry_run)
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Capture CLI/package evidence."""
    mgr = _require_backend(args)
    try:
        if hasattr(mgr, 'inspect_environment'):
            path = mgr.inspect_environment(args.version, output=args.output, dry_run=args.dry_run)
            print(path)
        else:
            print(f"ERROR: inspect not supported for {mgr.BACKEND_NAME}", file=sys.stderr)
            return 2
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


# Stable weekly-check launcher, written by `launchd install` into the machine's live-config
# tree (<live-config>/../launchd/backend-check.sh) — NEVER into the installing copy: the plist
# used to hardcode this script's own path (manage-backend.py:457 area), which for an installed
# plugin is a versioned cache dir that dies at the next plugin update. The launcher resolves
# everything (brew, free-agents root) at RUN time. bash-3.2 compatible.
LAUNCHER_TEMPLATE = """#!/usr/bin/env bash
# backend-check.sh — STABLE weekly backend-update check (free-agents, Task 11).
# Written by `manage-backend.py launchd install`; the plist points HERE, not at any plugin copy.
# Run time: (1) Homebrew on PATH via `brew shellenv` (launchd's PATH lacks the prefix — the old
# job failed nightly with `brew: command not found`); (2) resolve the free-agents root:
# $FA_ROOT if set (must be a root, else exit 1 naming it) -> the checkout the `csl` launcher
# symlink points into -> newest ~/.claude/plugins/cache/*/free-agents/<ver> by `sort -V`;
# (3) run scripts/check-backend-updates.sh, then bin/fa-post-upgrade-check.sh (report only).
# `--print-root` runs only (1)-(2) and prints the root (used by `launchd status`).
# Env: FA_BREW overrides brew discovery (must be executable, else exit 1 naming it).
set -u
log() { printf '%s backend-check: %s\\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

BREW="${FA_BREW:-}"
if [ -n "$BREW" ]; then
    if [ ! -x "$BREW" ]; then
        log "FAIL: FA_BREW=$BREW is set but not executable"
        exit 1
    fi
else
    for c in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [ -x "$c" ]; then BREW="$c"; break; fi
    done
fi
if [ -z "$BREW" ]; then
    log "FAIL: no brew found at /opt/homebrew/bin/brew or /usr/local/bin/brew (set FA_BREW)"
    exit 1
fi
eval "$("$BREW" shellenv)"

ROOT=""
if [ -n "${FA_ROOT:-}" ]; then
    if [ -f "$FA_ROOT/.claude-plugin/plugin.json" ]; then
        ROOT="$FA_ROOT"
    else
        log "FAIL: FA_ROOT=$FA_ROOT is set but is not a free-agents root (no .claude-plugin/plugin.json)"
        exit 1
    fi
else
    csl="$(command -v csl 2>/dev/null || true)"
    if [ -n "$csl" ]; then
        real="$(readlink -f "$csl" 2>/dev/null || true)"
        if [ -n "$real" ]; then
            cand="$(dirname "$(dirname "$real")")"
            if [ -f "$cand/.claude-plugin/plugin.json" ]; then ROOT="$cand"; fi
        fi
    fi
    if [ -z "$ROOT" ]; then
        hit="$(ls -d "$HOME"/.claude/plugins/cache/*/free-agents/*/ 2>/dev/null | sort -V | tail -1)"
        if [ -n "$hit" ] && [ -f "${hit%/}/.claude-plugin/plugin.json" ]; then ROOT="${hit%/}"; fi
    fi
fi
if [ -z "$ROOT" ]; then
    log "FAIL: could not resolve a free-agents root (set FA_ROOT)"
    exit 1
fi
log "root: $ROOT"

if [ "${1:-}" = "--print-root" ]; then
    printf '%s\\n' "$ROOT"
    exit 0
fi

rc=0
bash "$ROOT/scripts/check-backend-updates.sh" || rc=1
bash "$ROOT/bin/fa-post-upgrade-check.sh" --quiet || rc=1
if [ "$rc" -ne 0 ]; then log "FAIL: backend check exited $rc"; fi
exit "$rc"
"""

LAUNCHD_LABEL = "com.haiggoh.backend-update-check"


def _launchd_paths(repo: Path) -> tuple[Path, Path]:
    """(launcher, plist) — the launcher lives in the machine's live-config tree, stable across
    plugin updates; the plist is the per-user LaunchAgent."""
    from managers.base import live_config_file
    live = live_config_file(repo)
    launcher = live.parent.parent / "launchd" / "backend-check.sh"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    return launcher, plist


def cmd_launchd(args: argparse.Namespace) -> int:
    """Manage launchd for automatic update checks."""
    import re
    import subprocess
    repo = Path(__file__).resolve().parents[1]
    launcher, plist_path = _launchd_paths(repo)

    if args.launchd_action == "install":
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(LAUNCHER_TEMPLATE)
        launcher.chmod(0o755)
        plist_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>{launcher}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>1</integer>
    <key>Hour</key>
    <integer>10</integer>
    <key>Minute</key>
    <integer>17</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{Path.home() / ".claude" / "logs" / "backend-update-check.out"}</string>
  <key>StandardErrorPath</key>
  <string>{Path.home() / ".claude" / "logs" / "backend-update-check.err"}</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>'''
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)
        if not os.environ.get("FA_LAUNCHD_NO_LOAD"):
            subprocess.run(["launchctl", "load", str(plist_path)], check=False)
        print(f"Installed stable launcher at {launcher}")
        print(f"Installed launchd plist at {plist_path} (points at the launcher, not this copy)")
        return 0

    elif args.launchd_action == "uninstall":
        if plist_path.exists():
            if not os.environ.get("FA_LAUNCHD_NO_LOAD"):
                subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
            plist_path.unlink()
            print("Uninstalled launchd plist")
        else:
            print("Launchd plist not found")
        return 0

    elif args.launchd_action == "status":
        print(f"launcher: {launcher} ({'present' if launcher.is_file() else 'MISSING — run: launchd install'})")
        root = subprocess.run(["bash", str(launcher), "--print-root"], capture_output=True, text=True)
        if root.returncode == 0:
            print(f"resolved root: {root.stdout.strip()}")
        else:
            print(f"root resolution failed: {(root.stderr or root.stdout).strip()}")
        listed = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
        print(f"Launchd service: {'INSTALLED' if LAUNCHD_LABEL in listed else 'NOT INSTALLED'}")
        pr = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                            capture_output=True, text=True)
        m = re.search(r"last exit code = (-?\d+)", pr.stdout)
        if m:
            print(f"last exit code: {m.group(1)}")
        return 0

    elif args.launchd_action == "run-once":
        if not launcher.is_file():
            print(f"ERROR: stable launcher {launcher} is missing — run `launchd install` first",
                  file=sys.stderr)
            return 2
        result = subprocess.run(["/bin/bash", str(launcher)])
        return result.returncode

    return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Handle commands that don't need a backend
    if args.command == "upgrade":
        # Backend-independent: --backend is accepted but not needed here.
        import backend_upgrades as bu
        if args.check:
            bu.check()
        if not args.yes and not sys.stdin.isatty():
            print("ERROR: upgrade asks yes/no and needs a terminal; pass --yes to run unattended", file=sys.stderr)
            return 2
        return bu.prompt(args.yes)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "health" and args.all:
        return cmd_health(args)
    if args.command == "launchd":
        # Backend-independent (manages the machine's job, not one backend's venvs).
        return cmd_launchd(args)
    if not args.backend:
        parser.error("the following arguments are required: --backend")

    if not args.command:
        parser.print_help()
        return 0

    # All other commands require a backend
    if args.command == "releases":
        return cmd_releases(args)
    elif args.command == "installed":
        return cmd_installed(args)
    elif args.command == "install":
        return cmd_install(args)
    elif args.command == "validate":
        return cmd_validate(args)
    elif args.command in ("check-updates", "updates"):
        return cmd_updates(args)
    elif args.command == "promote":
        return cmd_promote(args)
    elif args.command == "remove":
        return cmd_remove(args)
    elif args.command == "smoke":
        return cmd_smoke(args)
    elif args.command == "snapshot":
        return cmd_snapshot(args)
    elif args.command == "inspect":
        return cmd_inspect(args)
    elif args.command == "health":
        return cmd_health(args)
    elif args.command == "repair":
        return cmd_repair(args)
    elif args.command == "info":
        return cmd_info(args)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())