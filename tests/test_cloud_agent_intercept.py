#!/usr/bin/env python3
"""hooks/cloud-agent-intercept.py through the REAL hook contract (stdin JSON -> stdout JSON).

Covers: free sessions untouched, opt-out untouched, opt-in denies with a redirect that RUNS, the
ask-once prompt on an undecided cloud session, fail-open on bad input, and that hooks.json
registers it on the Agent matcher. State and plugin root are temp paths.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "cloud-agent-intercept.py"


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.state = t / "asked"
        self.root = t / "plugin"
        (self.root / "bin").mkdir(parents=True)
        self.got = t / "got.json"
        tool = self.root / "bin" / "free-agent-tool.py"
        tool.write_text("import sys\nopen(%r,'w').write(sys.stdin.read())\nprint('{\"content\":\"ok\"}')\n" % str(self.got))

    def run_hook(self, payload, **env):
        e = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_BASE_URL", "FA_REPLACE_AGENTS")}
        e.update(FA_INTERCEPT_STATE=str(self.state), CLAUDE_PLUGIN_ROOT=str(self.root), **env)
        r = subprocess.run([sys.executable, str(HOOK)], input=payload if isinstance(payload, str) else json.dumps(payload),
                           capture_output=True, text=True, env=e, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    AGENT = {"tool_name": "Agent", "tool_input": {"prompt": "it's \"done\" $HOME `x`", "description": "probe",
                                                   "subagent_type": "code-reviewer"}}

    def test_free_sessions_untouched(self):
        for base in ("http://localhost:4141", "http://127.0.0.1:8000"):
            self.assertIsNone(self.run_hook(self.AGENT, ANTHROPIC_BASE_URL=base, FA_REPLACE_AGENTS="1"))
        self.assertFalse(self.state.exists(), "a free session never triggers the ask")

    def test_opt_out_untouched(self):
        self.assertIsNone(self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example", FA_REPLACE_AGENTS="0"))

    def test_other_tools_untouched(self):
        self.assertIsNone(self.run_hook({"tool_name": "Bash", "tool_input": {}}, FA_REPLACE_AGENTS="1"))

    def test_opt_in_denies_with_a_redirect_that_runs(self):
        out = self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example", FA_REPLACE_AGENTS="1")
        self.assertEqual(out["permissionDecision"], "deny")
        reason = out["permissionDecisionReason"]
        # Execute the exact command lines the model is told to run (the heredoc block).
        lines = reason.splitlines()
        start = next(i for i, l in enumerate(lines) if "free-agent-tool.py" in l)
        delim = lines[start].rsplit("<<'", 1)[1].rstrip("'")
        end = lines.index(delim, start + 1)
        cmd = "\n".join(l.strip() if i == 0 else l for i, l in enumerate(lines[start:end + 1]))
        subprocess.run(["bash", "-c", cmd], check=True, timeout=10)
        sent = json.loads(self.got.read_text())
        self.assertEqual(sent["prompt"], self.AGENT["tool_input"]["prompt"], "prompt passed verbatim")
        self.assertEqual(sent["subagent_type"], "code-reviewer")

    def test_native_login_counts_as_cloud(self):
        out = self.run_hook(self.AGENT, FA_REPLACE_AGENTS="1")
        self.assertEqual(out["permissionDecision"], "deny")

    def test_undecided_asks_exactly_once(self):
        first = self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example")
        self.assertEqual(first["permissionDecision"], "ask")
        self.assertIn("replace_agents", first["permissionDecisionReason"])
        self.assertTrue(self.state.exists())
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertIsNone(self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example"))

    def test_fail_open_on_garbage(self):
        self.assertIsNone(self.run_hook("not json", FA_REPLACE_AGENTS="1"))

    def test_help_does_not_read_stdin(self):
        r = subprocess.run([sys.executable, str(HOOK), "--help"], capture_output=True, text=True, timeout=5,
                           stdin=subprocess.PIPE)
        self.assertEqual(r.returncode, 0)
        self.assertIn("FA_REPLACE_AGENTS", r.stdout)
        self.assertEqual(subprocess.run([sys.executable, str(HOOK), "--bogus"], capture_output=True).returncode, 2)

    def test_registered_on_agent_matcher(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
        entry = [h for h in hooks if any("cloud-agent-intercept.py" in x["command"] for x in h["hooks"])]
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry[0]["matcher"], "Agent")


if __name__ == "__main__":
    unittest.main()
