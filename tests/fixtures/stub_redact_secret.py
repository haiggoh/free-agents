#!/usr/bin/env python3
"""Hermetic stand-in for audit-loose-ends redact-secret.py --scan-only.

Output format copied from the REAL scanner (2026-11-20):
  "<path> would redact 1: sk-ant… len=103 sha=8752d70f"
Flags any "sk-ant-" token. STUB_SCANNER_RC=<n> forces that exit code (failure-path tests).
Usage: stub_redact_secret.py [--help] --scan-only FILE...
"""
import os
import re
import sys

args = sys.argv[1:]
if "--help" in args or "-h" in args:
    print(__doc__)
    sys.exit(0)
if not args or args[0] != "--scan-only":
    print("usage: stub_redact_secret.py --scan-only FILE...", file=sys.stderr)
    sys.exit(2)
rc = int(os.environ.get("STUB_SCANNER_RC", "0"))
if rc:
    print("stub scanner forced failure", file=sys.stderr)
    sys.exit(rc)
n = 0
for path in args[1:]:
    for tok in re.findall(r"sk-ant-\S+", open(path, encoding="utf-8").read()):
        n += 1
        print(f"{path} would redact {n}: sk-ant… len={len(tok)} sha=00000000")
print(f"would redact {n} occurrence(s) across {len(args) - 1} named file(s), {len(args) - 1} examined")
