#!/usr/bin/env python3
"""Test that deleted local-stack-update-check.sh is not referenced in code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_no_code_references():
    """Confirm no code references to local-stack-update-check exist."""
    repo_root = Path(__file__).resolve().parent.parent

    # Search in code files (not docs, CHANGELOG, plans)
    result = subprocess.run(
        ["git", "grep", "-n", "local-stack-update-check"],
        cwd=repo_root,
        capture_output=True,
        text=True
    )

    # git grep returns 1 if no matches, 0 if matches found
    if result.returncode == 1:
        print("PASS: No references to local-stack-update-check found")
        return True

    # Check if any matches are in actual code (not docs/CHANGELOG/plans)
    lines = result.stdout.strip().split('\n')
    code_references = []
    for line in lines:
        if any(skip in line for skip in [
            '.md:', 'CHANGELOG', 'docs/', 'plan', 'Kopie',
            '.superpowers/sdd/', 'task-'
        ]):
            continue
        code_references.append(line)

    if code_references:
        print("FAIL: Code references found:\n" + "\n".join(code_references))
        return False

    print("PASS: No code references to local-stack-update-check (only docs/history)")
    return True


def test_package_release_no_reference():
    """Confirm scripts/package-release.sh doesn't reference it."""
    pkg_release = Path(__file__).resolve().parent.parent / "scripts" / "package-release.sh"
    if not pkg_release.exists():
        print("FAIL: scripts/package-release.sh not found")
        return False

    content = pkg_release.read_text(encoding="utf-8")
    if "local-stack-update-check" in content:
        print("FAIL: scripts/package-release.sh references local-stack-update-check")
        return False

    print("PASS: scripts/package-release.sh has no reference")
    return True


if __name__ == "__main__":
    passed = 0
    failed = 0

    for test_func in [test_no_code_references, test_package_release_no_reference]:
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