#!/usr/bin/env python3
"""Tests for manage-backend.py subcommand parity.

This test derives the subcommand list from the argparse object and asserts
each dispatches to a corresponding cmd_* function.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import manage-backend.py
MODULE_PATH = Path(__file__).resolve().parent.parent / "install" / "manage-backend.py"
spec = importlib.util.spec_from_file_location("manage_backend", MODULE_PATH)
assert spec and spec.loader
manage_backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = manage_backend
spec.loader.exec_module(manage_backend)


def get_subcommands(parser) -> list[str]:
    """Extract all subcommand names from the parser, including aliases."""
    subcommands = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for choice_name, choice_parser in action.choices.items():
                subcommands.append(choice_name)
    return subcommands


def get_dispatched_commands(module) -> dict[str, str]:
    """Map subcommand names to their dispatch function names in main()."""
    import inspect
    source = inspect.getsource(module.main)
    dispatched = {}

    # Parse the main function to find command dispatches
    # Look for patterns like: elif args.command == "cmd": return cmd_cmd(args)
    import re
    # Find all elif/else-if branches that dispatch commands
    pattern = r'(?:elif|if)\s+args\.command\s*==\s*["\']([^"\']+)["\']\s*:\s*return\s+(\w+)\('
    matches = re.findall(pattern, source)
    for cmd, func in matches:
        dispatched[cmd] = func

    # Also handle the "in" case for aliases like "check-updates", "updates"
    pattern2 = r'args\.command\s+in\s+\(([^)]+)\)\s*:\s*return\s+(\w+)\('
    matches2 = re.findall(pattern2, source)
    for cmds_str, func in matches2:
        for cmd in cmds_str.split(","):
            cmd = cmd.strip().strip('"\'')
            dispatched[cmd] = func

    return dispatched


def test_subcommand_parity():
    """Test that every subcommand has a dispatch in main()."""
    import argparse

    parser = manage_backend.build_parser()
    subcommands = get_subcommands(parser)

    # Get dispatched commands from main()
    dispatched = get_dispatched_commands(manage_backend)

    # "list" is handled specially (doesn't need backend)
    # "updates" is an alias of "check-updates"
    all_expected = set(subcommands)

    # Check that each subcommand has a dispatch
    missing = []
    for cmd in subcommands:
        if cmd not in dispatched:
            missing.append(cmd)

    if missing:
        print(f"FAIL: Subcommands missing dispatch in main(): {missing}")
        return False

    print(f"PASS: All {len(subcommands)} subcommands have dispatches")
    return True


def test_dispatch_calls():
    """Test that each command dispatches to the correct cmd_* function."""
    import argparse

    parser = manage_backend.build_parser()
    subcommands = get_subcommands(parser)

    # Test each subcommand by monkeypatching its cmd_* function
    for cmd in subcommands:
        if cmd == "list":
            continue  # list doesn't need backend
        if cmd == "updates":
            continue  # alias of check-updates

        # Find the expected function name
        dispatched = get_dispatched_commands(manage_backend)
        func_name = dispatched.get(cmd)
        if not func_name:
            print(f"FAIL: {cmd} has no dispatch function")
            return False

        # Verify the function exists
        if not hasattr(manage_backend, func_name):
            print(f"FAIL: {cmd} dispatches to non-existent {func_name}")
            return False

        # Monkeypatch the function and verify it's called
        mock = MagicMock(return_value=0)
        with patch.object(manage_backend, func_name, mock):
            # Build args that would trigger this command
            # We need --backend for most commands
            if cmd in ("releases", "installed", "install", "validate",
                       "check-updates", "promote", "remove", "smoke",
                       "snapshot", "inspect", "info"):
                # These need a backend - use a fake one that will fail early
                test_argv = ["--backend", "rapid-mlx", cmd]
                if cmd in ("install", "validate", "promote", "remove", "smoke", "snapshot", "inspect", "info"):
                    test_argv.append("0.15.3")
                if cmd == "releases":
                    test_argv.extend(["--limit", "1"])
            elif cmd == "launchd":
                test_argv = ["--backend", "rapid-mlx", "launchd", "status"]
            else:
                test_argv = ["--backend", "rapid-mlx", cmd]

            try:
                manage_backend.main(test_argv)
            except SystemExit as e:
                if e.code != 0 and e.code != 2:
                    pass  # Expected to fail due to missing backend setup
            except Exception:
                pass  # Expected to fail due to missing backend setup

            if not mock.called:
                print(f"FAIL: {cmd} did not call {func_name}")
                return False

    print("PASS: All subcommands dispatch correctly")
    return True


def test_updates_alias():
    """Test that 'updates' is an alias of 'check-updates'."""
    parser = manage_backend.build_parser()

    # Both should be valid subcommands
    args1 = parser.parse_args(["--backend", "rapid-mlx", "check-updates"])
    args2 = parser.parse_args(["--backend", "rapid-mlx", "updates"])

    assert args1.command == "check-updates"
    assert args2.command == "updates"  # argparse keeps alias name

    # Verify both dispatch to the same function in main()
    # Check the main() source for the dispatch
    import inspect
    source = inspect.getsource(manage_backend.main)
    assert 'args.command in ("check-updates", "updates")' in source or \
           'args.command in (\'check-updates\', \'updates\')' in source

    print("PASS: 'updates' is an alias of 'check-updates'")
    return True


if __name__ == "__main__":
    import argparse
    passed = 0
    failed = 0

    for test_func in [test_subcommand_parity, test_dispatch_calls, test_updates_alias]:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"FAIL: {test_func.__name__} raised {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)