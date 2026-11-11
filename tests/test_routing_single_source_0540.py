#!/usr/bin/env python3
"""0.54.0 Task 8 — Agent AND Workflow replacement derived from the offload skill (no drift).

The routing text lives ONCE in the marked block (fa:routing:begin/end) of
skills/offload/SKILL.md; bin/offload_routing.py reads it and both hooks emit it verbatim —
a test goes RED if any hook text and the block diverge. Workflow calls are routed per
route[workflow]. The honest-exhaustion mark is claimed ATOMICALLY (os.rename to an in-flight
file keyed by tool_use_id) and released only by PostToolUse, so a call vetoed by another hook
no longer burns the one allowed paid call.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "hooks"))
import offload_routing  # noqa: E402
import fa_fallback_mark as fm  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "lib"))
from hermetic_env import hermetic_env  # noqa: E402   (L2: one clean-env helper)


def run_hook(payload, root, cfg, extra_env=None):
    # 0.56.7 (L2): hermetic_env instead of dict(os.environ) — the old copy inherited every
    # LA_* (incl. LA_ROUTE_AGENT, scrubbed by hand below) and the real fence vars.
    env = hermetic_env({
        "ANTHROPIC_BASE_URL": "https://gateway.invalid", "CLAUDE_CONFIG_DIR": cfg,
        "FA_ROUTE_AGENT": "free-first", "FA_ROUTE_WORKFLOW": "free-first",
        "CLAUDE_PLUGIN_ROOT": str(root)})
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(ROOT / "hooks" / "cloud-agent-intercept.py")],
                          input=json.dumps(payload), capture_output=True, text=True,
                          env=env, cwd=ROOT, timeout=30)


class RoutingBlockTests(unittest.TestCase):
    def test_routing_block_is_complete(self):
        block = offload_routing.routing_block(str(ROOT))
        self.assertNotEqual(block, offload_routing.MISSING)
        self.assertLessEqual(len(block.encode()), 1500, "routing block must stay <= 1,500 bytes")
        for literal in ("free-agent-tool.py", "remote-session.sh <alias>",
                        "brief-the-delegate", "paid_fallback_allowed", "dispatching-parallel-agents",
                        "config/local-agent-system-prompt.txt",
                        "config/remote-agent-system-prompt.txt"):
            self.assertIn(literal, block, f"routing block missing {literal!r}")
        low = block.lower()
        self.assertTrue("announce" in low or "tell the user" in low, "announce-before-offload missing")
        self.assertIn("model", low)
        # no model NAMES (rosters rot) — point to --list instead
        self.assertIn("--list", block)

    def test_skill_has_no_second_routing_copy(self):
        raw = (ROOT / "skills/offload/SKILL.md").read_text(encoding="utf-8")
        i = raw.find(offload_routing.BEGIN)
        j = raw.find(offload_routing.END)
        self.assertGreaterEqual(i, 0)
        self.assertGreater(j, i)
        inside = raw[raw.find("-->", i) + 3: j]
        outside = raw[:i] + raw[raw.find("-->", i) + 3:] if False else (raw[:i] + raw[j:])
        self.assertEqual(raw.count("Default routing on a PAID session"), 1)
        self.assertIn("Default routing on a PAID session", inside)
        self.assertNotIn("Default routing on a PAID session", outside)

    def test_missing_markers_emit_loud_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "skills/offload").mkdir(parents=True)
            (root / "skills/offload/SKILL.md").write_text("# offload\nno markers here\n")
            self.assertEqual(offload_routing.routing_block(str(root)), offload_routing.MISSING)
            self.assertIn("routing block missing", offload_routing.MISSING)


class OffloadReportingDocs(unittest.TestCase):
    """2026-11-08 route-watcher user ask 2: the two VISIBLE reports for background offloads are
    a contract, so the skill text pinning them is test-guarded (milestone QC found the section
    was documentation-only; deleting it used to leave the suite green)."""

    def test_offload_skill_pins_the_two_visible_reports(self):
        raw = (ROOT / "skills" / "offload" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Reporting background offloads", raw)
        self.assertIn("run_in_background", raw)
        # launch report: the Bash description carries the resolved model/provider/effort
        self.assertIn("<model> @", raw)
        self.assertIn("(effort <lvl>)", raw)
        # completion report: visible text, and the exit-code convention spelled out
        self.assertIn("VISIBLE", raw)
        self.assertIn("exit code 0", raw)

    def test_remote_system_prompt_pins_the_qc_lesson(self):
        # 2026-11-08 route-watcher: green-for-the-wrong-reason is the top free-lane failure;
        # the free sessions' own system prompt must carry the red-first / mutation-RED rule
        raw = (ROOT / "config" / "remote-agent-system-prompt.txt").read_text(encoding="utf-8")
        self.assertIn("watched FAILING first", raw)
        self.assertIn("turn the test RED", raw)
        notes = (ROOT / "skills" / "free-session-handover" / "references"
                 / "operational-notes.md").read_text(encoding="utf-8")
        self.assertIn("QC before claiming done", notes)
        self.assertIn("OPTIONAL", notes)


class InterceptRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fa-routing-0540-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = tempfile.mkdtemp(prefix="fa-cfg-")
        self.addCleanup(shutil.rmtree, self.cfg, True)

    def test_intercept_deny_contains_routing_block_verbatim(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                   "tool_input": {"prompt": "review this", "subagent_type": "general-purpose",
                                  "description": "d"}, "tool_use_id": "toolu_1", "session_id": "s1"}
        r = run_hook(payload, ROOT, self.cfg)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(offload_routing.routing_block(str(ROOT)), reason)
        self.assertIn("free-agent-tool.py", reason)           # the dispatch heredoc stays
        # the free-SESSION route offered as a ready-to-run command (alias resolved, --effort set)
        self.assertRegex(reason, r'remote-session\.sh \S+ --effort \S+ -p "<brief>"')

    def test_workflow_routed_per_route_workflow(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Workflow",
                   "tool_input": {"name": "wf", "script": "x"}, "tool_use_id": "toolu_2",
                   "session_id": "s1"}
        r = run_hook(payload, ROOT, self.cfg)
        out = json.loads(r.stdout)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(offload_routing.routing_block(str(ROOT)), reason)
        self.assertNotIn("FA_AGENT_JSON", reason, "Workflow must not get the Agent-only heredoc")
        self.assertRegex(reason, r'remote-session\.sh \S+ --effort \S+ -p "<brief>"')
        # paid route -> pass through (no output, exit 0)
        r2 = run_hook(payload, ROOT, self.cfg, extra_env={"FA_ROUTE_WORKFLOW": "paid"})
        self.assertEqual(r2.returncode, 0)
        self.assertEqual(r2.stdout.strip(), "")
        # free session -> pass through
        r3 = run_hook(payload, ROOT, self.cfg, extra_env={"ANTHROPIC_BASE_URL": "http://localhost:4141"})
        self.assertEqual(r3.stdout.strip(), "")

    def test_missing_markers_hook_emits_loud_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "hooks", root / "hooks")
            (root / "bin").mkdir()
            shutil.copy(ROOT / "bin/offload_routing.py", root / "bin/offload_routing.py")
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                       "tool_input": {"prompt": "hi"}, "tool_use_id": "t", "session_id": "s"}
            r = run_hook(payload, root, self.cfg)
            out = json.loads(r.stdout)
            self.assertIn(offload_routing.MISSING,
                          out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_hook_fails_open_on_garbage(self):
        r = subprocess.run([sys.executable, str(ROOT / "hooks/cloud-agent-intercept.py")],
                           input="not json", capture_output=True, text=True,
                           env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(ROOT)}, cwd=ROOT, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")


class MarkClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fa-mark-0540-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._old = os.environ.get("FA_FALLBACK_MARK_DIR")
        os.environ["FA_FALLBACK_MARK_DIR"] = self.tmp
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("FA_FALLBACK_MARK_DIR", None)
        else:
            os.environ["FA_FALLBACK_MARK_DIR"] = self._old

    def test_two_parallel_claims_allow_only_one(self):
        fm.write_mark("s1", [{"id": "g", "provider": "gemini", "ok": False}], now=1000.0)
        first = fm.claim_mark("s1", "toolu_A", now=1000.0)
        second = fm.claim_mark("s1", "toolu_B", now=1000.0)
        self.assertIsNotNone(first)
        self.assertIsNone(second, "the atomic rename must let exactly one claim through")

    def test_vetoed_call_restores_mark_after_ttl(self):
        fm.write_mark("s1", [{"id": "g", "ok": False}], ttl_s=900, now=1000.0)
        self.assertIsNotNone(fm.claim_mark("s1", "toolu_A", now=1000.0))
        # no PostToolUse arrives (another hook vetoed the call): at TTL+ the mark returns
        self.assertIsNone(fm.claim_mark("s1", "toolu_B", now=1000.0 + 899))
        restored = fm.claim_mark("s1", "toolu_B", now=1000.0 + 901)
        self.assertIsNotNone(restored, "an expired in-flight claim must be restored to an available mark")

    def test_mark_consumed_after_the_call_ran(self):
        fm.write_mark("s1", [{"id": "g", "ok": False}], now=1000.0)
        self.assertIsNotNone(fm.claim_mark("s1", "toolu_A", now=1000.0))
        self.assertTrue(fm.release_inflight("s1", "toolu_A"))
        self.assertIsNone(fm.claim_mark("s1", "toolu_B", now=1000.0),
                          "PostToolUse ran: the mark stays consumed")


if __name__ == "__main__":
    unittest.main()
