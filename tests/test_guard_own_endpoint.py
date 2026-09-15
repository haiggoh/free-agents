#!/usr/bin/env python3
"""Tests for hooks/guard-own-endpoint.py -- the PreToolUse own-endpoint guard.

These drive the hook the way Claude Code does: a JSON payload on stdin, a
decision on stdout. The script's own --self-test covers the decision function
directly; this exercises the real subprocess boundary, which is where a hook
actually fails (bad JSON shape, wrong key names, a non-zero exit that wedges
the tool call).
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "hooks" / "guard-own-endpoint.py"

# The literal command that killed three sessions on 2026-09-14/15.
INCIDENT_CMD = 'pkill -f "litellm.*4141"'


def run(command, base_url=None, tool="Bash", extra_env=None):
    """Invoke the guard as a hook. Returns (returncode, parsed_or_None)."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_BASE_URL", None)
    env.pop("LA_GUARD_DISABLE", None)
    env.pop("LA_GUARD_EXTRA_PORTS", None)
    if base_url is not None:
        env["ANTHROPIC_BASE_URL"] = base_url
    env.update(extra_env or {})
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}})
    p = subprocess.run([sys.executable, str(GUARD)], input=payload,
                       capture_output=True, text=True, env=env)
    out = p.stdout.strip()
    return p.returncode, (json.loads(out) if out else None)


def decision(parsed):
    if not parsed:
        return "allow"
    return parsed["hookSpecificOutput"]["permissionDecision"]


class GuardTests(unittest.TestCase):

    def test_denies_the_exact_incident_command(self):
        code, parsed = run(INCIDENT_CMD, "http://localhost:4141")
        self.assertEqual(code, 0, "a hook must exit 0 even when denying")
        self.assertEqual(decision(parsed), "deny")
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("4141", reason, "the reason must name the port")
        self.assertIn("THIS session", reason)

    def test_emits_the_contract_shape(self):
        _, parsed = run(INCIDENT_CMD, "http://localhost:4141")
        hso = parsed["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertIn("permissionDecision", hso)
        self.assertIn("permissionDecisionReason", hso)

    def test_cloud_endpoint_is_never_guarded(self):
        # A remote gateway cannot be killed from this machine, so guarding it
        # would only produce false denials.
        _, parsed = run(INCIDENT_CMD, "https://llmgw.joyia.p7s1.io")
        self.assertEqual(decision(parsed), "allow")

    def test_unset_endpoint_fails_open(self):
        _, parsed = run(INCIDENT_CMD, None)
        self.assertEqual(decision(parsed), "allow")

    def test_a_different_port_is_allowed(self):
        _, parsed = run("pkill -f 'rapid-mlx.*8001'", "http://localhost:8000")
        self.assertEqual(decision(parsed), "allow")

    def test_local_inference_port_is_guarded_too(self):
        _, parsed = run("pkill -f 'rapid-mlx.*8000'", "http://localhost:8000")
        self.assertEqual(decision(parsed), "deny")

    def test_inspection_is_not_blocked(self):
        # Reading about the port must stay allowed, or the guard makes the
        # endpoint undebuggable.
        for cmd in ("tail -50 /tmp/x/proxy-4141.log",
                    "curl -s http://localhost:4141/v1/models",
                    "lsof -nP -iTCP:4141 -sTCP:LISTEN",
                    "grep -n 4141 notes.txt",
                    "cat /tmp/x/proxy-4141.yaml"):
            with self.subTest(cmd=cmd):
                _, parsed = run(cmd, "http://localhost:4141")
                self.assertEqual(decision(parsed), "allow")

    def test_our_own_stopper_aimed_at_our_port_is_denied(self):
        _, parsed = run("remote-session.sh --stop 4141", "http://localhost:4141")
        self.assertEqual(decision(parsed), "deny")

    def test_pid_file_kill_is_denied(self):
        _, parsed = run("kill $(cat /tmp/x/proxy-4141.pid)", "http://localhost:4141")
        self.assertEqual(decision(parsed), "deny")

    def test_non_bash_tool_is_ignored(self):
        code, parsed = run(INCIDENT_CMD, "http://localhost:4141", tool="Read")
        self.assertEqual(code, 0)
        self.assertEqual(decision(parsed), "allow")

    def test_disable_switch(self):
        _, parsed = run(INCIDENT_CMD, "http://localhost:4141",
                        extra_env={"LA_GUARD_DISABLE": "1"})
        self.assertEqual(decision(parsed), "allow")

    def test_extra_ports_are_protected(self):
        _, parsed = run("pkill -f 'litellm.*4142'", "http://localhost:4141",
                        extra_env={"LA_GUARD_EXTRA_PORTS": "4142"})
        self.assertEqual(decision(parsed), "deny")

    def test_malformed_stdin_fails_open(self):
        p = subprocess.run([sys.executable, str(GUARD)], input="not json",
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, "a broken guard must not wedge the session")
        self.assertEqual(p.stdout.strip(), "")

    def test_help_and_unknown_flag(self):
        h = subprocess.run([sys.executable, str(GUARD), "--help"],
                           capture_output=True, text=True)
        self.assertEqual(h.returncode, 0)
        self.assertIn("guard-own-endpoint.py", h.stdout)
        # An unrecognised flag must not fall through into doing the work.
        b = subprocess.run([sys.executable, str(GUARD), "--bogus"],
                           capture_output=True, text=True)
        self.assertNotEqual(b.returncode, 0)
        self.assertIn("unknown option", b.stderr)

    def test_self_test_passes(self):
        s = subprocess.run([sys.executable, str(GUARD), "--self-test"],
                           capture_output=True, text=True)
        self.assertEqual(s.returncode, 0, s.stdout)
        self.assertIn("0 failed", s.stdout)

    def test_registered_in_hooks_json(self):
        d = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        entries = d["hooks"]["PreToolUse"]
        cmds = [c["command"] for e in entries for c in e["hooks"]]
        self.assertTrue(any("guard-own-endpoint.py" in c for c in cmds),
                        "the guard must be registered or it never runs")
        self.assertTrue(any(e.get("matcher") == "Bash" for e in entries))


if __name__ == "__main__":
    unittest.main(verbosity=2)
