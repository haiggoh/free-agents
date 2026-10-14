#!/usr/bin/env python3
"""The spoofed identity contract for remote sessions (0.32.0).

remote-session.sh launches `claude --model <SPOOF>`. Before 0.32.0 that flag was
hardcoded to claude-opus-5, so the proxy roster's claude-opus-5-5 entry (added for
1M context support) was never actually requested and every session was costed at
Opus 5's rates. The primary is now version-gated: claude-opus-5-5 on Claude Code
>= 2.1.280 (1M support), claude-opus-5 as the compatibility fallback — including
when the version CANNOT be probed, because an unprovable upgrade must never break
a session that worked.

`--print-spoof` is the observability seam these tests drive: it prints the primary
id the launcher would use and exits — no proxy, no network, no key access.

Usage: python3 tests/remote/test_spoof_primary.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "..", "bin", "remote-session.sh")

passed = failed = 0


def check(cond, label):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS:", label)
    else:
        failed += 1
        print("  FAIL:", label)


def spoof_with(claude_version, failing=False):
    """Run --print-spoof with a stub `claude` whose --version prints the given
    string (failing=True: the stub exits 1 with no output, i.e. the version cannot
    be probed at all). The stub dir goes FIRST on PATH so it shadows any real
    claude, while the inherited PATH keeps supplying the bash >= 4 the script's
    shebang needs (declare -A, process substitution) and every other helper."""
    stub = tempfile.mkdtemp(prefix="spoof-stub-")
    if claude_version is not None or failing:
        p = os.path.join(stub, "claude")
        if failing:
            body = "exit 1\n"
        else:
            body = "[ \"$1\" = --version ] && printf '%%s\\n' \"%s\"\n" % (claude_version,)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(p, 0o755)
    env = dict(os.environ)
    env["PATH"] = stub + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    return subprocess.run([SCRIPT, "--print-spoof"], capture_output=True,
                          text=True, env=env, timeout=120)


def test_spoof_primary():
    """pytest entry: the checks below ran at import time (with a sys.exit) until 0.34.0,
    which aborted collection of the WHOLE suite — `pytest -q tests` ran zero tests."""
    global passed, failed
    passed = failed = 0
    out = spoof_with("2.1.291 (Claude Code)")
    check(out.returncode == 0 and out.stdout.strip() == "claude-opus-5-5",
          "CC >= 2.1.280 requests claude-opus-5-5 (1M support), got %r" % out.stdout.strip())

    out = spoof_with("2.1.296.b9d (Claude Code)")
    check(out.returncode == 0 and out.stdout.strip() == "claude-opus-5-5",
          "a 4-segment version string (this machine's real output) still reads as capable, got %r"
          % out.stdout.strip())

    out = spoof_with("2.1.100 (Claude Code)")
    check(out.returncode == 0 and out.stdout.strip() == "claude-opus-5",
          "old CC keeps the claude-opus-5 compatibility fallback, got %r" % out.stdout.strip())

    out = spoof_with(None, failing=True)
    check(out.returncode == 0 and out.stdout.strip() == "claude-opus-5",
          "an unprobeable version also falls back (never breaks a working session), got %r"
          % out.stdout.strip())

    assert failed == 0, "%d spoof check(s) failed" % failed


if __name__ == "__main__":
    try:
        test_spoof_primary()
    except AssertionError:
        pass
    print("passed %d, failed %d" % (passed, failed))
    sys.exit(1 if failed else 0)
