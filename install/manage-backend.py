#!/usr/bin/env python3
"""Unified Backend Manager CLI.

Manage versioned backend runtimes (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm)
through a common interface.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add install directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

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
    import managers.litellm  # noqa: F401
except ImportError:
    pass

from managers import (
    BackendManager,
    ManagerError,
    get_manager,
    get_manager_instance,
    list_managers,
)


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
  manage-backend.py --backend rapid-mlx promote 0.15.3
  manage-backend.py --backend litellm install 1.60.0
        """.strip(),
    )
    parser.add_argument("--home", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="print the plan without executing")
    parser.add_argument("--backend", choices=list_managers(), required=True, help="backend to manage")
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

    # validate command
    val_p = sub.add_parser("validate", help="validate an installed version")
    val_p.add_argument("version")

    # check-updates command
    chk_p = sub.add_parser("check-updates", help="check for package updates")

    # promote command (for backends that support pin promotion)
    prom_p = sub.add_parser("promote", help="promote pins to a version")
    prom_p.add_argument("version")

    # remove command
    rem_p = sub.add_parser("remove", help="remove an installed version")
    rem_p.add_argument("version")
    rem_p.add_argument("--yes", action="store_true", help="skip confirmation")

    # info command
    info_p = sub.add_parser("info", help="show detailed info about a version")
    info_p.add_argument("version")

    # launchd command
    launchd_p = sub.add_parser("launchd", help="manage launchd for automatic update checks")
    launchd_sub = launchd_p.add_subparsers(dest="launchd_action", required=True)
    launchd_sub.add_parser("install", help="install launchd plist for weekly update checks")
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
                "version": version,
                "target": str(mgr.target_for(version)),
                "installation_mode": "locked_recreation" if lock else "fresh_resolution",
            }, indent=2))
            return 0

        info = mgr.install_version(
            version,
            python=args.python,
            refresh_deps=args.refresh_deps,
            dry_run=False,
        )
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


def cmd_launchd(args: argparse.Namespace) -> int:
    """Manage launchd for automatic update checks."""
    import subprocess
    from pathlib import Path

    # Script is in the scripts directory, not install directory
    SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "check-backend-updates.sh"

    if args.launchd_action == "install":
        # Install launchd plist
        plist_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.haiggoh.backend-update-check</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>{SCRIPT_PATH}</string>
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

        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.haiggoh.backend-update-check.plist"
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)
        subprocess.run(["launchctl", "load", str(plist_path)], check=False)
        print(f"Installed launchd plist at {plist_path}")
        return 0

    elif args.launchd_action == "uninstall":
        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.haiggoh.backend-update-check.plist"
        if plist_path.exists():
            subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
            plist_path.unlink()
            print("Uninstalled launchd plist")
        else:
            print("Launchd plist not found")
        return 0

    elif args.launchd_action == "status":
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        if "com.haiggoh.backend-update-check" in result.stdout:
            print("Launchd service: INSTALLED and RUNNING")
        else:
            print("Launchd service: NOT INSTALLED or NOT RUNNING")
        return 0

    elif args.launchd_action == "run-once":
        # Run the update check script directly
        import subprocess
        result = subprocess.run(["/bin/bash", str(Path(__file__).parent.parent / "scripts" / "check-backend-updates.sh")])
        return result.returncode

    return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Handle commands that don't need a backend
    if args.command == "list":
        return cmd_list(args)

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
    elif args.command == "updates":
        return cmd_updates(args)
    elif args.command == "info":
        return cmd_info(args)
    elif args.command == "launchd":
        return cmd_launchd(args)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())