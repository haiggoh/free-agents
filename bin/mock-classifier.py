#!/usr/bin/env python3
"""Mock classifier for blind-trust auto mode (Option B).

This script mimics the classifier interface but ALWAYS returns "allow",
bypassing the real classifier entirely. Used when LA_BLIND_AUTO=1
to achieve true blind-trust behavior while keeping --permission-mode auto.

The real classifier is consulted via a subprocess call from the Claude Code
auto-mode machinery. This mock replaces that call with an instant "allow".

Usage:
    LA_CLASSIFIER_CMD="python3 /path/to/mock-classifier.py" claude --permission-mode auto

The classifier protocol (from Claude Code):
- Reads JSON from stdin: {"tool": "Bash", "args": {"command": "..."}, "context": {...}}
- Writes JSON to stdout: {"decision": "allow"|"deny"|"ask", "reason": "..."}
"""
import json
import sys


def main() -> int:
    # Read the classification request (tool call details)
    try:
        raw = sys.stdin.read()
        if raw:
            request = json.loads(raw)
        else:
            request = {}
    except Exception:
        request = {}

    # Always allow — this is the "blind trust" part
    response = {
        "decision": "allow",
        "reason": "blind-trust mock: auto-allowed"
    }

    # Write response to stdout
    json.dump(response, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())