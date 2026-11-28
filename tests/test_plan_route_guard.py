#!/usr/bin/env python3
"""Task 2 (plan-route-guard): the PreToolUse guard hooks/plan-route-guard.py.

A PAID session cannot Write/Edit/MultiEdit a plan file (docs/superpowers/plans/**, or
~/.claude/plans/**, >=2 Task/Step/Phase headings) whose tasks lack a route tag. Free sessions
(localhost base URL) never block; plan_check=0 never blocks; any internal error fails OPEN.
RED before the hook exists.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))


def _load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("plan_route_guard", ROOT / "hooks" / "plan-route-guard.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["plan_route_guard"] = mod   # dataclass PEP-563 lookup needs the registry entry
    spec.loader.exec_module(mod)
    return mod


guard = _load()

PLAN_ALL_TAGGED = """# Plan
## Task 1: Alpha — free:dispatch
body
## Task 2: Beta — cloud:spec
body
"""

PLAN_UNTAGGED = """# Plan
## Task 1: Alpha — free:session
body
## Task 2: Beta
body
"""

PLAN_TAG_IN_LIST = """# Plan
## Task 1: Alpha
- free:session
body
## Task 2: Beta — cloud:verify
body
"""

PLAN_INVALID_CLOUD = """# Plan
## Task 1: Alpha — free:session
## Task 2: Beta — cloud:tool use is never a reason
"""

PLAN_ZERO_FREE_NO_ROUTING = """# Plan
## Task 1: Alpha — cloud:spec
## Task 2: Beta — cloud:review
"""

PLAN_ZERO_FREE_WITH_ROUTING = """# Plan
Routing: everything here is cloud:spec by design, see the design doc
## Task 1: Alpha — cloud:spec
## Task 2: Beta — cloud:review
"""

PLAN_ZERO_FREE_BAD_ROUTING = """# Plan
Routing: cloud:tool use, not delegatable
## Task 1: Alpha — cloud:spec
## Task 2: Beta — cloud:review
"""


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.plan = self.home / ".claude" / "plans" / "x.md"
        self.plan.parent.mkdir(parents=True)
        self._old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        cfg = self.home / "cfg"
        cfg.mkdir()
        os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._old_cfg

    def env(self, **kw):
        e = {"HOME": str(self.home), "ANTHROPIC_BASE_URL": "https://paid-gateway.example",
             "CLAUDE_PLUGIN_ROOT": str(ROOT)}
        e.update(kw)
        return e

    def test_all_tagged_plan_allowed(self):
        self.assertIsNone(guard.decide(str(self.plan), PLAN_ALL_TAGGED, self.env()))

    def test_untagged_task_denied_and_named(self):
        reason = guard.decide(str(self.plan), PLAN_UNTAGGED, self.env())
        self.assertIsNotNone(reason)
        self.assertIn("tasks without a route tag", reason)
        self.assertIn("## Task 2: Beta", reason)

    def test_tag_in_first_list_item_counts(self):
        self.assertIsNone(guard.decide(str(self.plan), PLAN_TAG_IN_LIST, self.env()))

    def test_invalid_cloud_reason_denied(self):
        # pinned independently of guard.INVALID_CLOUD so deleting an entry from the tuple
        # turns this test RED (the plan's mutation check)
        for bad in ("tool use", "tool-use", "tools", "too hard", "too complex",
                    "local is slow", "slow", "not delegatable"):
            text = PLAN_INVALID_CLOUD.replace("cloud:tool use is never a reason", f"cloud:{bad}")
            reason = guard.decide(str(self.plan), text, self.env())
            self.assertIsNotNone(reason, f"INVALID_CLOUD {bad!r} must deny")
            self.assertIn("invalid cloud reason", reason)

    def test_valid_cloud_reasons_allowed(self):
        for ok in ("spec", "verify", "review", "security"):
            text = PLAN_ALL_TAGGED.replace("cloud:spec", f"cloud:{ok}")
            self.assertIsNone(guard.decide(str(self.plan), text, self.env()), ok)

    def test_zero_free_needs_routing_line(self):
        reason = guard.decide(str(self.plan), PLAN_ZERO_FREE_NO_ROUTING, self.env())
        self.assertIsNotNone(reason)
        self.assertIn("no free: task and no valid Routing: line", reason)

    def test_zero_free_with_valid_routing_line_allowed(self):
        self.assertIsNone(guard.decide(str(self.plan), PLAN_ZERO_FREE_WITH_ROUTING, self.env()))

    def test_routing_line_with_invalid_reason_denied(self):
        reason = guard.decide(str(self.plan), PLAN_ZERO_FREE_BAD_ROUTING, self.env())
        self.assertIsNotNone(reason)

    def test_legacy_local_and_remote_tags_accepted(self):
        text = PLAN_ALL_TAGGED.replace("free:dispatch", "local:dispatch").replace(
            "cloud:spec", "remote:review")
        self.assertIsNone(guard.decide(str(self.plan), text, self.env()))

    def test_role_suffix_accepted(self):
        text = PLAN_ALL_TAGGED.replace("free:dispatch", "free:dispatch/validator")
        self.assertIsNone(guard.decide(str(self.plan), text, self.env()))

    def test_single_heading_md_is_not_a_plan(self):
        text = "# Just a readme\n\nno task headings\n"
        self.assertIsNone(guard.decide(str(self.plan), text, self.env()))

    def test_non_plan_path_ignored(self):
        self.assertIsNone(guard.decide(str(self.home / "notes.md"), PLAN_UNTAGGED, self.env()))

    def test_free_session_never_blocks(self):
        env = self.env(ANTHROPIC_BASE_URL="http://localhost:4141")
        self.assertIsNone(guard.decide(str(self.plan), PLAN_UNTAGGED, env))

    def test_plan_check_off_never_blocks(self):
        sys.path.insert(0, str(ROOT / "bin"))
        import cloud_session_env as cse
        cse.save_plan_check(False)
        self.assertIsNone(guard.decide(str(self.plan), PLAN_UNTAGGED, self.env()))

    # --- rulings recovered from the deleted plan ledger (peer handover 2026-11-28) ----------

    def test_left_boundary_toll_free_is_not_a_tag(self):
        text = "# P\n## Task 1: Alpha\ntoll-free:session is not a tag\n\n## Task 2: Beta — cloud:spec\n"
        reason = guard.decide(str(self.plan), text, self.env())
        self.assertIsNotNone(reason, "toll-free: must not count as a tag (left boundary)")

    def test_nested_step_inherits_enclosing_task_tag(self):
        text = ("# P\n## Task 1: Alpha — free:session\n### Step 1.1\nbody\n\n"
                "## Task 2: Beta — cloud:spec\n")
        self.assertIsNone(guard.decide(str(self.plan), text, self.env()))

    def test_tag_in_third_non_empty_line_counts(self):
        text = ("# P\n## Task 1: Alpha\nsome intro\nmore intro\n- free:session\n\n"
                "## Task 2: Beta — cloud:spec\n")
        self.assertIsNone(guard.decide(str(self.plan), text, self.env()))

    def test_empty_cloud_reason_invalid(self):
        text = "# P\n## Task 1: Alpha — free:session\n## Task 2: Beta — cloud:\n"
        reason = guard.decide(str(self.plan), text, self.env())
        self.assertIsNotNone(reason)
        self.assertIn("invalid cloud reason", reason)

    def test_cloud_reason_stops_at_next_tag(self):
        text = ("# P\n## Task 1: Alpha — free:session\n"
                "## Task 2: Beta — cloud:spec, see free:session notes on tool use\n")
        self.assertIsNone(guard.decide(str(self.plan), text, self.env()),
                          "the reason must stop at the next route tag on the line")

    def test_edit_judged_on_resulting_file(self):
        self.plan.write_text(PLAN_UNTAGGED)
        fix = {"file_path": str(self.plan), "old_string": "## Task 2: Beta\n",
               "new_string": "## Task 2: Beta — cloud:spec\n"}
        text = guard.resulting_text("Edit", fix)
        self.assertIsNotNone(text)
        self.assertIsNone(guard.decide(str(self.plan), text, self.env()),
                          "an Edit that adds the tag is judged on the resulting file")
        bad = {"file_path": str(self.plan), "old_string": "## Task 2: Beta\n",
               "new_string": "## Task 2: Beta (renamed)\n"}
        text = guard.resulting_text("Edit", bad)
        self.assertIsNotNone(guard.decide(str(self.plan), text, self.env()),
                             "an Edit that leaves the task untagged is denied")

    def test_resulting_text_honours_replace_all(self):
        self.plan.write_text(PLAN_UNTAGGED.replace("## Task 2", "## Task 0"))
        text = guard.resulting_text(
            "Edit", {"file_path": str(self.plan), "old_string": "## Task 0",
                     "new_string": "## Task 2: Beta — cloud:spec", "replace_all": True})
        self.assertIn("## Task 2: Beta — cloud:spec", text)

    def test_resulting_text_unreadable_file_allows(self):
        self.assertIsNone(guard.resulting_text("Edit", {"file_path": "/nope/x.md",
                                                        "old_string": "a", "new_string": "b"}))


class FailOpenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.plan = self.home / ".claude" / "plans" / "x.md"
        self.plan.parent.mkdir(parents=True)
        self._old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        cfg = self.home / "cfg"
        cfg.mkdir()
        os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._old_cfg

    def _run(self, stdin, extra_env=None):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
        env["ANTHROPIC_BASE_URL"] = "https://paid-gateway.example"
        if extra_env:
            env.update(extra_env)
        return subprocess.run([sys.executable, str(ROOT / "hooks" / "plan-route-guard.py")],
                              input=stdin, capture_output=True, text=True, env=env, timeout=60)

    def test_corrupt_settings_fails_open(self):
        (self.home / "cfg" / "settings.json").write_text("{ not json")
        payload = json.dumps({"tool_name": "Write", "tool_input": {
            "file_path": str(self.plan), "content": PLAN_UNTAGGED}})
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, "a corrupt settings file must never block")
        self.assertIn("failing open", r.stderr)

    def test_garbage_stdin_fails_open(self):
        r = self._run("not json {{{")
        self.assertEqual(r.returncode, 0)
        self.assertIn("failing open", r.stderr)

    def test_write_untagged_plan_denied_via_stdin(self):
        payload = json.dumps({"tool_name": "Write", "tool_input": {
            "file_path": str(self.plan), "content": PLAN_UNTAGGED}})
        r = self._run(payload)
        self.assertEqual(r.returncode, 2)
        self.assertIn("plan_check", r.stderr)
        self.assertIn("## Task 2: Beta", r.stderr)
        self.assertIn("set plan_check 0", r.stderr)


class RegistrationTests(unittest.TestCase):
    def test_free_rule_parity_with_intercept(self):
        intercept = _load_named("cloud_agent_intercept",
                                ROOT / "hooks" / "cloud-agent-intercept.py")
        for url in ("http://localhost:4141", "http://127.0.0.1:9", "https://gateway.example",
                    "", None):
            self.assertEqual(guard._is_free(url), intercept.is_free_session(url), repr(url))

    def test_hooks_json_registers_guard(self):
        doc = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        entries = doc["hooks"]["PreToolUse"]
        entry = next((e for e in entries if e.get("matcher") == "Write|Edit|MultiEdit"), None)
        self.assertIsNotNone(entry, "PreToolUse needs a Write|Edit|MultiEdit entry")
        cmds = [h.get("command", "") for h in entry.get("hooks", [])]
        self.assertTrue(any("plan-route-guard.py" in c for c in cmds), cmds)
        self.assertTrue(all(h.get("timeout") == 5 for h in entry["hooks"]))

    def test_help_runs(self):
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / "plan-route-guard.py"), "--help"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("plan", r.stdout.lower())


def _load_named(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    unittest.main()
