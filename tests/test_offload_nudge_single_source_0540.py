#!/usr/bin/env python3
"""0.54.0 Task 9 — the SessionStart nudge's routing text is GENERATED from the offload
skill's marked block (single source), so a skill edit can never drift from the nudge.

Only the free-first and free-only variants changed: each is now its one-line mode sentence +
routing_block(). The paid variant and the free-lane council nudge keep their exact text.
All hook tests run as SUBPROCESSES under hermetic_env (hook tests never inherit the session env).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import offload_routing  # noqa: E402

HOOK = ROOT / "hooks" / "offload-nudge.py"


sys.path.insert(0, str(ROOT / "tests" / "lib"))
from hermetic_env import hermetic_env  # noqa: E402   (L2: one clean-env helper)


def run_nudge(extra_env=None, cfg=None):
    cfg = cfg or tempfile.mkdtemp(prefix="fa-nudge-cfg-")
    # 0.56.7 (L2): hermetic_env — hook tests never inherit the session env
    env = hermetic_env({"ANTHROPIC_BASE_URL": "https://gateway.invalid",
                        "CLAUDE_CONFIG_DIR": cfg, "CLAUDE_PLUGIN_ROOT": str(ROOT)})
    env.update(extra_env or {})
    r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True,
                       env=env, cwd=ROOT, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


class NudgeSingleSourceTests(unittest.TestCase):
    def test_free_first_nudge_contains_routing_block_verbatim(self):
        text = run_nudge({"FA_ROUTE_AGENT": "free-first"})
        block = offload_routing.routing_block(str(ROOT))
        self.assertIn(block, text)
        self.assertIn("FREE OFFLOADING IS", text)     # the mode sentence stays

    def test_free_only_nudge_contains_routing_block_verbatim(self):
        text = run_nudge({"FA_ROUTE_AGENT": "free-only"})
        self.assertIn(offload_routing.routing_block(str(ROOT)), text)
        self.assertIn("FREE ONLY", text)

    def test_nudge_names_parallel_fanout_route(self):
        text = run_nudge({"FA_ROUTE_AGENT": "free-first"})
        self.assertIn("dispatching-parallel-agents", text)

    def test_free_lane_keeps_council_nudge_without_routing_block(self):
        text = run_nudge({"ANTHROPIC_BASE_URL": "http://localhost:4141"})
        self.assertIn("already runs on FREE inference", text)
        self.assertNotIn(offload_routing.routing_block(str(ROOT)), text)
        # a leaked CLAUDE_IS_LOCAL=1 on a GATEWAY url must NOT switch to the free-lane text
        text2 = run_nudge({"FA_ROUTE_AGENT": "free-first", "CLAUDE_IS_LOCAL": "1"})
        self.assertIn("PAID inference", text2)
        self.assertNotIn("already runs on FREE inference", text2)

    def test_paid_mode_text_unchanged(self):
        text = run_nudge({"FA_ROUTE_AGENT": "paid"})
        self.assertIn("Agent path is set to PAID", text)
        self.assertNotIn(offload_routing.routing_block(str(ROOT)), text,
                         "the paid variant must keep its own text (no routing block)")

    def test_nudge_size_budget(self):
        block = offload_routing.routing_block(str(ROOT))
        self.assertLessEqual(len(block.encode()), 1500)
        text = run_nudge({"FA_ROUTE_AGENT": "free-first"})
        self.assertLessEqual(len(text.encode()), 4000, "the whole nudge stays compact")

    def test_no_model_names_in_routing_block(self):
        block = offload_routing.routing_block(str(ROOT))
        r = subprocess.run([sys.executable, str(ROOT / "bin/free-agent-tool.py"), "--list"],
                           capture_output=True, text=True, timeout=30)
        names = set()
        for m in re.finditer(r"^\s*\d+\.\s+(.+?)\s{2,}", r.stdout, flags=re.M):
            names.add(m.group(1).strip())
        # also bare family/model tokens the roster prints in brackets
        for m in re.finditer(r"\[remote \w+ · [^\]]*?; ([a-z0-9]+);", r.stdout):
            names.add(m.group(1))
        self.assertTrue(names, "the roster listing must yield names to check against")
        for name in names:
            self.assertNotIn(name, block, f"model/family name {name!r} leaked into the routing block")


if __name__ == "__main__":
    unittest.main()
