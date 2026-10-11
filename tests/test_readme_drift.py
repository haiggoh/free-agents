#!/usr/bin/env python3
"""Tests for README drift - verify all CLI commands in README are valid."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

# Import manage-backend.py
MODULE_PATH = Path(__file__).resolve().parent.parent / "install" / "manage-backend.py"
spec = importlib.util.spec_from_file_location("manage_backend", MODULE_PATH)
assert spec and spec.loader
manage_backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = manage_backend
spec.loader.exec_module(manage_backend)

# Also import manage-rapid-mlx.py wrapper
WRAPPER_PATH = Path(__file__).resolve().parent.parent / "install" / "manage-rapid-mlx.py"
spec2 = importlib.util.spec_from_file_location("manage_rapid_mlx", WRAPPER_PATH)
assert spec2 and spec2.loader
manage_rapid_mlx = importlib.util.module_from_spec(spec2)
sys.modules[spec2.name] = manage_rapid_mlx
spec2.loader.exec_module(manage_rapid_mlx)


def get_registered_subcommands():
    """Get all registered subcommands from the parser."""
    parser = manage_backend.build_parser()
    subcommands = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for choice_name in action.choices:
                subcommands.append(choice_name)
    return set(subcommands)


def extract_readme_commands():
    """Extract all manage-backend.py and manage-rapid-mlx.py commands from README."""
    readme_path = Path(__file__).resolve().parent.parent / "README.md"
    content = readme_path.read_text(encoding="utf-8")

    commands = []

    # Pattern: manage-backend.py --backend XXX <subcommand> ...
    # Pattern: manage-rapid-mlx.py <subcommand> ...
    # Also: ./install/manage-rapid-mlx.py <subcommand> ...

    # Find commands in bash code blocks (```bash ... ```)
    bash_blocks = re.findall(r'```bash\n(.*?)\n```', content, re.DOTALL)
    for block in bash_blocks:
        lines = block.strip().split('\n')
        for line in lines:
            line = line.strip()
            if 'manage-backend.py' in line:
                # Extract subcommand after backend
                match = re.search(r'manage-backend\.py\s+(?:--backend\s+\S+\s+)?(\w+)', line)
                if match:
                    commands.append(('manage-backend.py', match.group(1)))
            elif 'manage-rapid-mlx.py' in line:
                match = re.search(r'manage-rapid-mlx\.py\s+(\w+)', line)
                if match:
                    commands.append(('manage-rapid-mlx.py', match.group(1)))

    # Also check backtick commands
    backtick_commands = re.findall(r'`([^`]+)`', content)
    for cmd in backtick_commands:
        if 'manage-backend.py' in cmd:
            match = re.search(r'manage-backend\.py\s+(?:--backend\s+\S+\s+)?(\w+)', cmd)
            if match:
                commands.append(('manage-backend.py', match.group(1)))
        elif 'manage-rapid-mlx.py' in cmd:
            match = re.search(r'manage-rapid-mlx\.py\s+(\w+)', cmd)
            if match:
                commands.append(('manage-rapid-mlx.py', match.group(1)))

    return commands


def test_readme_commands_registered():
    """Test that all commands mentioned in README are registered subcommands."""
    import argparse

    registered = get_registered_subcommands()
    readme_commands = extract_readme_commands()

    missing = []
    for source, cmd in readme_commands:
        if cmd not in registered:
            missing.append(f"{source}: {cmd}")

    if missing:
        print(f"FAIL: Commands in README not registered: {missing}")
        print(f"Registered subcommands: {sorted(registered)}")
        return False

    print(f"PASS: All {len(readme_commands)} README commands are registered subcommands")
    return True


if __name__ == "__main__":
    import argparse
    passed = 0
    failed = 0

    for test_func in [test_readme_commands_registered]:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"FAIL: {test_func.__name__} raised {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)