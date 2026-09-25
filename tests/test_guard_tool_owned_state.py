#!/usr/bin/env python3
"""Tests for hooks/guard-tool-owned-state.py -- the PreToolUse plugin-state guard.

Drives the hook the way Claude Code does: a JSON payload on stdin, a decision on stdout.
--self-test covers the decision function; this exercises the subprocess boundary and the
hooks.json registration, which is where a hook actually fails.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "hooks" / "guard-tool-owned-state.py"


def run(tool, tool_input, extra_env=None, raw=None):
    env = dict(os.environ)
    env.pop("LA_STATE_GUARD_DISABLE", None)
    env.update(extra_env or {})
    stdin = raw if raw is not None else json.dumps({"tool_name": tool, "tool_input": tool_input})
    p = subprocess.run([sys.executable, str(GUARD)], input=stdin, capture_output=True, text=True, env=env)
    out = p.stdout.strip()
    return p.returncode, (json.loads(out) if out else None)


def denied(parsed):
    return bool(parsed) and parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


class GuardToolOwnedState(unittest.TestCase):
    def test_self_test_passes(self):
        p = subprocess.run([sys.executable, str(GUARD), "--self-test"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_incident_write_is_denied_with_version_bump_advice(self):
        rc, out = run("Write", {"file_path": os.path.expanduser("~/.claude/plugins/installed_plugins.json"),
                                "content": "{}"})
        self.assertEqual(rc, 0)
        self.assertTrue(denied(out))
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("VERSION BUMP", reason)
        self.assertIn("get-haiggoh apply", reason)

    def test_incident_bash_cp_is_denied(self):
        _, out = run("Bash", {"command": "! cp /tmp/installed_plugins.json ~/.claude/plugins/installed_plugins.json"})
        self.assertTrue(denied(out))

    def test_read_is_allowed_silently(self):
        rc, out = run("Bash", {"command": "cat ~/.claude/plugins/installed_plugins.json"})
        self.assertEqual((rc, out), (0, None))

    def test_claude_config_dir_is_honoured(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "plugins", "installed_plugins.json")
            _, out = run("Edit", {"file_path": target}, {"CLAUDE_CONFIG_DIR": d})
            self.assertTrue(denied(out))
            # ...and the default root is then NOT the protected one.
            _, out = run("Edit", {"file_path": os.path.expanduser("~/.claude/plugins/installed_plugins.json")},
                         {"CLAUDE_CONFIG_DIR": d})
            self.assertIsNone(out)

    def test_disable_env_allows(self):
        _, out = run("Write", {"file_path": os.path.expanduser("~/.claude/plugins/installed_plugins.json")},
                     {"LA_STATE_GUARD_DISABLE": "1"})
        self.assertIsNone(out)

    def test_malformed_payload_fails_open(self):
        rc, out = run(None, None, raw="not json")
        self.assertEqual((rc, out), (0, None))

    def test_unknown_flag_does_not_run(self):
        p = subprocess.run([sys.executable, str(GUARD), "--bogus"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_hooks_json_has_no_duplicate_keys(self):
        # Two branches each adding "PreToolUse" merge cleanly in git; json.loads then keeps only the
        # LAST one and silently drops the other guard.
        def no_dupes(pairs):
            keys = [k for k, _ in pairs]
            dup = {k for k in keys if keys.count(k) > 1}
            self.assertFalse(dup, f"duplicate keys in hooks.json: {dup}")
            return dict(pairs)
        json.loads((ROOT / "hooks" / "hooks.json").read_text(), object_pairs_hook=no_dupes)

    def test_both_pretooluse_guards_registered(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
        cmds = " ".join(c["command"] for h in hooks for c in h["hooks"])
        self.assertIn("guard-own-endpoint.py", cmds)
        self.assertIn("guard-tool-owned-state.py", cmds)

    def test_shared_rules_carry_the_no_partial_rewrite_rule(self):
        # The hook covers the registry files; the prose rule covers every OTHER file a model might
        # "restore" by retyping it. Both launchers load this file into every free session.
        text = (ROOT / "config" / "shared-agent-shipping-rules.txt").read_text()
        self.assertIn("NEVER REWRITE A FILE FROM A PARTIAL VIEW", text)
        self.assertIn("MISSING VERSION BUMP", text)
        self.assertIn("does NOT escape the sandbox", text)

    def test_registered_in_hooks_json_for_every_file_tool(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
        entry = [h for h in hooks if any("guard-tool-owned-state.py" in c["command"] for c in h["hooks"])]
        self.assertEqual(len(entry), 1)
        tools = set(entry[0]["matcher"].split("|"))
        self.assertTrue({"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"} <= tools)


if __name__ == "__main__":
    unittest.main()
