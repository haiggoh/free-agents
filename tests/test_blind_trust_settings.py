#!/usr/bin/env python3
"""Tests for bin/blind-trust-settings.py — the single blind-trust settings generator."""

from __future__ import annotations
import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "blind-trust-settings.py"
spec = importlib.util.spec_from_file_location("bts", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError("Failed to load blind-trust-settings module")
bts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bts)

PASSES = 0
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    global PASSES
    if cond:
        PASSES += 1
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}")


# Test 1: bypass mechanism sets bypassPermissions
s = bts.build_settings(["Bash(ls:*)", "mcp__joyia__*"], ["Bash(ls:*)", "Read"], "bypass", False)
check(s["permissions"]["defaultMode"] == "bypassPermissions", "bypass mechanism sets bypassPermissions")
check(s["permissions"]["allow"] == ["Bash(ls:*)", "Read"], "allow merged, de-duplicated, mcp dropped without --enable-mcp")
check(s["permissions"]["deny"] == bts.DESTRUCTIVE_DENY, "destructive deny list always present")
check("sandbox" not in s, "generator does not decide the sandbox")

# Test 2: --enable-mcp keeps mcp allow rules
m = bts.build_settings(["mcp__joyia__*"], [], "bypass", True)
check("mcp__joyia__*" in m["permissions"]["allow"], "--enable-mcp keeps mcp allow rules")

# Test 3: hook mechanism keeps auto mode and emits allow
h = bts.build_settings([], [], "hook", False)
check(h["permissions"]["defaultMode"] == "auto", "hook mechanism keeps auto mode")
hook_cmd = h["permissions"]["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
# Verify the command structure (it doesn't read stdin, just prints allow)
check("permissionDecision" in hook_cmd and "allow" in hook_cmd, "hook command contains allow verdict")

# Test 4: missing allowlist files degrade to empty lists, still writes; file is mode 600
with tempfile.TemporaryDirectory() as td:
    outp = Path(td) / "s.json"
    r = subprocess.run([sys.executable, str(SCRIPT), "--master", "/nonexistent", "--profile", "/nonexistent",
                        "--out", str(outp)], capture_output=True, text=True)
    check(r.returncode == 0 and outp.exists(), "missing allowlist files degrade to empty lists, still writes")
    check(stat.S_IMODE(outp.stat().st_mode) == 0o600, "settings file is mode 600")
    # Test 4b: warning printed to stderr for missing master file
    check("warning" in r.stderr.lower() and "not found" in r.stderr.lower(), "warning on stderr for missing master")
    # Test 4c: warning printed to stderr for missing profile file (two warnings)
    check(r.stderr.count("warning") >= 2, "warning on stderr for both missing files")

# Test 4d: invalid JSON prints warning
with tempfile.TemporaryDirectory() as td:
    bad_master = Path(td) / "bad.json"
    bad_master.write_text("{invalid json")
    outp = Path(td) / "s.json"
    r = subprocess.run([sys.executable, str(SCRIPT), "--master", str(bad_master), "--profile", "/nonexistent",
                        "--out", str(outp)], capture_output=True, text=True)
    check(r.returncode == 0 and outp.exists(), "invalid JSON degrades to empty list, still writes")
    check("warning" in r.stderr.lower() and "invalid json" in r.stderr.lower(), "warning on stderr for invalid JSON")

    # Test 5: unknown flag exits non-zero
    bad = subprocess.run([sys.executable, str(SCRIPT), "--bogus"], capture_output=True, text=True)
    check(bad.returncode != 0, "unknown flag exits non-zero")

    # Test 6: --help documents flags
    hl = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    check(hl.returncode == 0 and "--mechanism" in hl.stdout, "--help documents flags")

print(f"\n{PASSES} passed, {len(FAILURES)} failed")
if __name__ == "__main__":
    raise SystemExit(1 if FAILURES else 0)