#!/usr/bin/env python3
"""hooks/cloud-agent-intercept.py through the REAL hook contract (stdin JSON -> stdout JSON).

Covers (0.43.0, cloud-screen plan Task 5): the three routing modes from cloud_session_env —
free-first (deny+redirect, ONE paid call after this session's free attempts are honestly
exhausted, via the fallback mark), free-only (deny, never falls back), paid (pass through) —
plus the legacy FA_REPLACE_AGENTS mapping, free sessions untouched, fail-open on bad input and
on a corrupt settings layer, and the Agent matcher in hooks.json. Config dir, mark dir, state
and plugin root are temp paths; the user's real ~/.claude is never read for a verdict."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "cloud-agent-intercept.py"
sys.path.insert(0, str(ROOT / "bin"))
import fa_fallback_mark as fm  # noqa: E402


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = t / "cfg"
        self.cfg.mkdir()
        self.marks = t / "marks"
        self.marks.mkdir()
        self.root = t / "plugin"
        (self.root / "bin").mkdir(parents=True)
        self.got = t / "got.json"
        tool = self.root / "bin" / "free-agent-tool.py"
        tool.write_text("import sys\nopen(%r,'w').write(sys.stdin.read())\nprint('{\"content\":\"ok\"}')\n" % str(self.got))

    def run_hook(self, payload, **env):
        e = {k: v for k, v in os.environ.items()
             if k not in ("ANTHROPIC_BASE_URL", "FA_REPLACE_AGENTS", "FA_ROUTE_AGENT",
                          "FA_ROUTE_WORKFLOW", "FA_FALLBACK_MARK_DIR", "CLAUDE_CONFIG_DIR")}
        e.update(CLAUDE_CONFIG_DIR=str(self.cfg), FA_FALLBACK_MARK_DIR=str(self.marks),
                 CLAUDE_PLUGIN_ROOT=str(self.root), **env)
        r = subprocess.run([sys.executable, str(HOOK)],
                           input=payload if isinstance(payload, str) else json.dumps(payload),
                           capture_output=True, text=True, env=e, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def write_settings(self, env_extra):
        (self.cfg / "settings.json").write_text(json.dumps({"env": env_extra}))

    AGENT = {"tool_name": "Agent", "tool_input": {"prompt": "it's \"done\" $HOME `x`", "description": "probe",
                                                  "subagent_type": "code-reviewer"}, "session_id": "sess-h1"}

    def test_free_sessions_untouched(self):
        for base in ("http://localhost:4141", "http://127.0.0.1:8000"):
            self.assertIsNone(self.run_hook(self.AGENT, ANTHROPIC_BASE_URL=base, FA_ROUTE_AGENT="free-only"))
        self.assertEqual(list(self.marks.iterdir()), [], "a free session never consumes a mark")

    def test_other_tools_untouched(self):
        self.assertIsNone(self.run_hook({"tool_name": "Bash", "tool_input": {}}, FA_ROUTE_AGENT="free-only"))

    def test_paid_mode_passes_through(self):
        self.assertIsNone(self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example",
                                        FA_ROUTE_AGENT="paid"))

    def test_legacy_opt_out_still_means_paid(self):
        self.assertIsNone(self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example",
                                        FA_REPLACE_AGENTS="0"))

    def test_free_first_default_denies_with_a_redirect_that_runs(self):
        out = self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example")
        self.assertEqual(out["permissionDecision"], "deny")
        reason = out["permissionDecisionReason"]
        lines = reason.splitlines()
        start = next(i for i, ln in enumerate(lines) if "free-agent-tool.py" in ln)
        delim = lines[start].rsplit("<<'", 1)[1].rstrip("'")
        end = lines.index(delim, start + 1)
        cmd = "\n".join(ln.strip() if i == 0 else ln for i, ln in enumerate(lines[start:end + 1]))
        subprocess.run(["bash", "-c", cmd], check=True, timeout=10)
        sent = json.loads(self.got.read_text())
        self.assertEqual(sent["prompt"], self.AGENT["tool_input"]["prompt"], "prompt passed verbatim")
        self.assertEqual(sent["subagent_type"], "code-reviewer")

    def test_free_only_denies_and_says_it_never_falls_back(self):
        self.write_settings({cse_key() : "free-only"})
        out = self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("never", out["permissionDecisionReason"].lower())

    def test_free_first_with_a_valid_mark_lets_exactly_one_call_through(self):
        old_dir = os.environ.get("FA_FALLBACK_MARK_DIR")
        os.environ["FA_FALLBACK_MARK_DIR"] = str(self.marks)   # write AND assertions in the sandbox
        try:
            fm.write_mark("sess-h1", [{"id": "gemini-3.8-flash", "provider": "gemini", "ok": False}])
            self.assertIsNone(self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example"),
                              "the armed mark lets the ONE native paid call through")
            self.assertFalse(fm.mark_path("sess-h1").exists(), "the mark is consumed (single use)")
        finally:
            if old_dir is None:
                os.environ.pop("FA_FALLBACK_MARK_DIR", None)
            else:
                os.environ["FA_FALLBACK_MARK_DIR"] = old_dir
        out = self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example")
        self.assertEqual(out["permissionDecision"], "deny", "the second call is redirected again")

    def test_undecided_is_never_asked(self):
        out = self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example")
        self.assertEqual(out["permissionDecision"], "deny", "an undecided user gets the redirect")
        self.assertNotEqual(out["permissionDecision"], "ask")
        out2 = self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example")
        self.assertEqual(out2["permissionDecision"], "deny", "and again — no ask-once state file")

    def test_expired_mark_does_not_arm_the_paid_path(self):
        old_dir = os.environ.get("FA_FALLBACK_MARK_DIR")
        os.environ["FA_FALLBACK_MARK_DIR"] = str(self.marks)
        try:
            fm.write_mark("sess-h1", [], ttl_s=1)
            # The SUBPROCESS decides expiry with its own clock, so backdate the file instead
            # of patching this process's clock.
            mp = fm.mark_path("sess-h1")
            doc = json.loads(mp.read_text())
            doc["written_at"] -= 60
            mp.write_text(json.dumps(doc))
            out = self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example")
        finally:
            if old_dir is None:
                os.environ.pop("FA_FALLBACK_MARK_DIR", None)
            else:
                os.environ["FA_FALLBACK_MARK_DIR"] = old_dir
        self.assertEqual(out["permissionDecision"], "deny")

    def test_fail_open_on_garbage(self):
        self.assertIsNone(self.run_hook("not json", FA_ROUTE_AGENT="free-only"))

    def test_fail_open_when_the_settings_layer_is_corrupt(self):
        (self.cfg / "settings.json").write_text("not an object{")
        self.assertIsNone(self.run_hook(self.AGENT, ANTHROPIC_BASE_URL="https://gw.example"),
                          "a corrupt settings layer never blocks the user's Agent")

    def test_native_login_counts_as_cloud(self):
        out = self.run_hook(self.AGENT, FA_ROUTE_AGENT="free-only")
        self.assertEqual(out["permissionDecision"], "deny")

    def test_help_names_the_modes(self):
        r = subprocess.run([sys.executable, str(HOOK), "--help"], capture_output=True, text=True, timeout=5,
                           stdin=subprocess.PIPE)
        self.assertEqual(r.returncode, 0)
        self.assertIn("FA_ROUTE_AGENT", r.stdout)
        self.assertIn("free-first", r.stdout)
        self.assertEqual(subprocess.run([sys.executable, str(HOOK), "--bogus"], capture_output=True).returncode, 2)

    def test_registered_on_agent_matcher(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
        entry = [h for h in hooks if any("cloud-agent-intercept.py" in x["command"] for x in h["hooks"])]
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry[0]["matcher"], "Agent")


def cse_key():
    from cloud_session_env import ROUTE_ENV
    return ROUTE_ENV["agent"]


if __name__ == "__main__":
    unittest.main()
