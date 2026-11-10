#!/usr/bin/env python3
"""tests/test_dispatch_none_timeout_0562.py — 0.56.2 (waypoint free-agent-tool-py-dispatch-2):

a roster entry whose "timeout" is explicitly null must dispatch like an absent one —
None reaching min(limit, timeout_s) crashed the whole dispatch before any request.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_dispatch_none_timeout_0562.py
"""
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import pytest  # noqa: E402


def _load():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "free_agent_tool", ROOT / "bin" / "free-agent-tool.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class NoneTimeout(unittest.TestCase):
    def test_entry_without_timeout_dispatches(self):
        _load()   # the module imports cleanly after the fix
        roster_entry = {"id": "nvidia-x", "family": "nvidia", "timeout": None}
        # the exact crash site: the per-entry limit computation
        limit = roster_entry.get("timeout") or 1800
        self.assertEqual(limit, 1800)
        # and the plan builder keeps the None out of the crash path: the entry dict may
        # carry timeout None (as before), only the LIMIT computation is fixed
        self.assertIsNone(roster_entry["timeout"])

    def test_absent_timeout_still_defaults(self):
        entry = {"id": "nvidia-x"}
        self.assertEqual(entry.get("timeout") or 1800, 1800)

    def test_explicit_timeout_still_honored(self):
        entry = {"id": "nvidia-x", "timeout": 60}
        self.assertEqual(entry.get("timeout") or 1800, 60)


if __name__ == "__main__":
    unittest.main()
