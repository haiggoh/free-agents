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
    )
    parser.add_argument(
        "--home", type=Path, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan without executing"
    )
    parser.add_argument(
        "--backend", choices=list_managers(), help="backend to manage (required for subcommands)"
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # list command
    sub.add_parser("list", help="list all registered backends")

    # releases command
    releases = sub.add_parser("releases", help="list installable releases for a backend")
    releases.add_argument("--pre", action="store_true", help="include prereleases")
    releases.add_argument("--limit", type=int, default=20, help="max releases to show")

    # installed command
    sub.add_parser("installed", help="list local versioned environments for a backend")

    # install command
    install_p = sub.add_parser("install", help="install/validate a release")
    install_p.add_argument("version", nargs="?", help="version to install (omit for interactive)")
    install_p.add_argument("--pre", action="store_true", help="offer prereleases in picker")
    install_p.add_argument("--python", help="base Python executable")
    install_p.add_argument("--refresh-deps", action="store_true", help="ignore existing recreation lock")
    install_p.add_argument("--skip-pin-update", action="store_true", help="install without promoting pins")

    # validate command
    validate_p = sub.add_parser("validate", help="validate an installed environment")
    validate_p.add_argument("version", help="version to validate")

    # updates command
    sub.add_parser("updates", help="check for available package updates")

    # info command
    info_p = sub.add_parser("info", help="show backend info")
    info_p.add_argument("version", nargs="?", help="version to inspect (default: latest installed)")

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
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())