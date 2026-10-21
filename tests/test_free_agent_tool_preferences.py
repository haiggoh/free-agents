#!/usr/bin/env python3
"""free-agent-tool.py picks dispatch models from the maintainer's preference order.

config/dispatch-preferences.json lists models in order; the first AVAILABLE one runs and a failed
dispatch falls over to the next. Every attempt is announced (model + route) on stderr, and the
JSON result names the model, its route and every attempt. Nothing is sent anywhere: availability
and the two dispatch paths are replaced with recorders.

Usage: python3 tests/test_free_agent_tool_preferences.py [-v]
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "bin" / "free-agent-tool.py"
spec = importlib.util.spec_from_file_location("free_agent_tool_prefs", TOOL)
fat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fat)


def prefs(*entries):
    return {"order": list(entries)}


R1 = {"id": "r1", "kind": "remote", "provider": "gemini", "model": "m1", "display": "Remote One",
      "smoke_tested": True}
R2 = {"id": "r2", "kind": "remote", "provider": "nvidia", "model": "m2", "display": "Remote Two",
      "smoke_tested": True}
UNTESTED = {"id": "u", "kind": "remote", "provider": "nvidia", "model": "mu", "display": "Untested",
            "smoke_tested": False}
L1 = {"id": "l1", "kind": "local", "alias": "loc-a", "display": "Local A", "smoke_tested": True}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.keys = Path(self.tmp.name) / "keys"
        self.keys.mkdir()
        self.env = {"LA_API_KEYS_DIR": str(self.keys)}

    def key(self, provider):
        (self.keys / provider).write_text("secret\n")


class LoadingTests(Base):
    def test_shipped_file_parses_in_the_maintainers_order(self):
        order = [e["id"] for e in fat.load_preferences({})["order"]]
        self.assertEqual(order[:5], ["gemini-3.8-flash", "nvidia-deepseek-v4.1-flash",
                                     "nvidia-glm-5.3", "nvidia-glm-5.3-flash", "nvidia-nemotron-3-ultra"])
        self.assertEqual(order[5:], ["local-deepseek-v4-flash", "local-laguna-s", "local-kat-coder"])

    def test_every_shipped_entry_has_a_family_and_known_roles(self):
        # Council routing only works when the roster is fully annotated: a new entry without
        # roles would silently serve every role, so the shipped file must set them.
        valid = {"operator", "reasoner", "validator", "utility"}
        for e in fat.load_preferences({})["order"]:
            self.assertTrue(e.get("family"), e["id"])
            self.assertTrue(e.get("roles"), e["id"])
            self.assertLessEqual(set(e["roles"]), valid, e["id"])

    def test_every_role_has_at_least_two_families(self):
        # A cross-family review needs a second family in every role.
        order = fat.load_preferences({})["order"]
        for role in ("operator", "reasoner", "validator", "utility"):
            fams = {e["family"] for e in order if role in e["roles"]}
            self.assertGreaterEqual(len(fams), 2, role)

    def test_env_path_overrides_the_shipped_file(self):
        p = Path(self.tmp.name) / "p.json"
        p.write_text(json.dumps(prefs(R2)))
        self.assertEqual(fat.load_preferences({"FA_DISPATCH_PREFS": str(p)})["order"][0]["id"], "r2")

    def test_unreadable_override_falls_back_to_shipped(self):
        got = fat.load_preferences({"FA_DISPATCH_PREFS": "/nonexistent/prefs.json"})
        self.assertEqual(got["order"][0]["id"], "gemini-3.8-flash")


class AvailabilityTests(Base):
    def test_remote_needs_its_provider_key(self):
        self.key("nvidia")
        got = fat.candidates(prefs(R1, R2), self.env, local_ok=lambda a: False)
        self.assertEqual([e["id"] for e in got], ["r2"])

    def test_untested_is_skipped_unless_opted_in(self):
        self.key("nvidia")
        self.assertEqual(fat.candidates(prefs(UNTESTED, R2), self.env, local_ok=lambda a: False)[0]["id"], "r2")
        env = dict(self.env, FA_DISPATCH_INCLUDE_UNTESTED="1")
        self.assertEqual(fat.candidates(prefs(UNTESTED, R2), env, local_ok=lambda a: False)[0]["id"], "u")

    def test_local_needs_weights_on_disk(self):
        self.assertEqual(fat.candidates(prefs(L1), self.env, local_ok=lambda a: False), [])
        self.assertEqual(fat.candidates(prefs(L1), self.env, local_ok=lambda a: a == "loc-a")[0]["id"], "l1")


class CouncilTests(Base):
    """Roles and model families (the council idea): a request may ask for a role, and an
    adversarial review may avoid the family that did the work."""

    A = dict(R1, family="google", roles=["operator", "validator"])
    B = dict(R2, family="zhipu", roles=["reasoner", "validator"])
    C = dict(L1, family="poolside", roles=["operator"])

    def ids(self, **req):
        self.key("gemini")
        self.key("nvidia")
        return [e["id"] for e in fat.candidates(prefs(self.A, self.B, self.C), self.env,
                                                local_ok=lambda a: True, **req)]

    def test_role_keeps_only_entries_that_list_it_in_order(self):
        self.assertEqual(self.ids(role="validator"), ["r1", "r2"])
        self.assertEqual(self.ids(role="operator"), ["r1", "l1"])

    def test_avoid_family_skips_that_family(self):
        self.assertEqual(self.ids(role="validator", avoid_family="google"), ["r2"])

    def test_entries_without_roles_serve_every_role(self):
        self.key("gemini")
        got = fat.candidates(prefs(R1), self.env, local_ok=lambda a: True, role="reasoner")
        self.assertEqual([e["id"] for e in got], ["r1"])

    def test_subagent_type_maps_to_a_role(self):
        self.assertEqual(fat.request_role({"subagent_type": "code-reviewer"}), "validator")
        self.assertEqual(fat.request_role({"role": "reasoner", "subagent_type": "coder"}), "reasoner")
        self.assertIsNone(fat.request_role({"subagent_type": "general-purpose"}))

    def test_shipped_roles_give_a_cross_family_validator(self):
        self.key("gemini")
        self.key("nvidia")
        got = fat.candidates(fat.load_preferences({}), self.env, local_ok=lambda a: True,
                             role="validator", avoid_family="google")
        self.assertTrue(got)
        self.assertNotIn("google", {e["family"] for e in got})


class FailoverTests(Base):
    def run_main(self, payload, outcomes):
        """outcomes: entry id -> (ok, text). Records the order attempts were made in."""
        self.key("gemini")
        self.key("nvidia")
        p = Path(self.tmp.name) / "p.json"
        p.write_text(json.dumps(prefs(R1, R2, L1)))
        env = dict(self.env, FA_DISPATCH_PREFS=str(p))
        tried = []

        def fake_dispatch(entry, prompt, env_):
            tried.append(entry["id"])
            self.prompts.append(prompt)
            ok, text = outcomes[entry["id"]]
            if not ok:
                raise fat.DispatchFailed(text)
            return text

        self.prompts = []
        out, err = io.StringIO(), io.StringIO()
        real = (fat.dispatch_entry, fat.local_on_disk)
        fat.dispatch_entry, fat.local_on_disk = fake_dispatch, (lambda a: True)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = fat.run(payload, env)
        finally:
            fat.dispatch_entry, fat.local_on_disk = real
        return code, tried, out.getvalue(), err.getvalue()

    def test_first_available_wins_and_is_announced(self):
        code, tried, out, err = self.run_main({"prompt": "hi"}, {"r1": (True, "answer")})
        self.assertEqual(code, 0)
        self.assertEqual(tried, ["r1"])
        res = json.loads(out)
        self.assertEqual((res["content"], res["model_used"], res["display"]), ("answer", "r1", "Remote One"))
        self.assertIn("Remote One", err)
        self.assertIn("stateless", err)

    def test_failure_falls_over_to_the_next_in_order(self):
        code, tried, out, err = self.run_main({"prompt": "hi"}, {"r1": (False, "HTTP 503"), "r2": (True, "ok")})
        self.assertEqual(code, 0)
        self.assertEqual(tried, ["r1", "r2"])
        res = json.loads(out)
        self.assertEqual(res["model_used"], "r2")
        self.assertEqual([a["id"] for a in res["attempts"]], ["r1", "r2"])
        self.assertIn("HTTP 503", res["attempts"][0]["error"])
        self.assertIn("Remote Two", err)

    def test_all_failing_exits_nonzero_listing_every_attempt(self):
        code, tried, out, err = self.run_main(
            {"prompt": "hi"}, {"r1": (False, "a"), "r2": (False, "b"), "l1": (False, "c")})
        self.assertNotEqual(code, 0)
        self.assertEqual(tried, ["r1", "r2", "l1"])
        self.assertIn("r1", err)
        self.assertIn("l1", err)

    def test_files_are_attached_to_the_prompt(self):
        f = Path(self.tmp.name) / "notes.txt"
        f.write_text("THE FILE BODY")
        self.run_main({"prompt": "review", "files": [str(f)]}, {"r1": (True, "ok")})
        self.assertIn("THE FILE BODY", self.prompts[0])
        self.assertIn("notes.txt", self.prompts[0])


class RemoteDispatchTests(Base):
    def test_remote_dispatch_uses_a_fresh_outdir_and_the_entry_settings(self):
        seen = []

        def fake_run(cmd, **_kw):
            seen.append(cmd)
            out = Path(cmd[cmd.index("--outdir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "output.txt").write_text(f"answer {len(seen)}")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        entry = dict(R1, reasoning_effort="medium", max_tokens=1234)
        real, fat.subprocess.run = fat.subprocess.run, fake_run
        try:
            a = fat.dispatch_entry(entry, "p", {})
            b = fat.dispatch_entry(entry, "p", {})
        finally:
            fat.subprocess.run = real
        self.assertEqual((a, b), ("answer 1", "answer 2"))   # never a previous run's output
        cmd = seen[0]
        self.assertEqual(cmd[cmd.index("--provider") + 1], "gemini")
        self.assertEqual(cmd[cmd.index("--model") + 1], "m1")
        self.assertEqual(cmd[cmd.index("--reasoning-effort") + 1], "medium")
        self.assertEqual(cmd[cmd.index("--max-tokens") + 1], "1234")
        self.assertNotEqual(seen[0][seen[0].index("--outdir") + 1], seen[1][seen[1].index("--outdir") + 1])

    def test_remote_nonzero_exit_is_a_failover(self):
        def fake_run(cmd, **_kw):
            return subprocess.CompletedProcess(cmd, 3, stdout="", stderr="✖ REMOTE gemini failed: HTTP 503")

        real, fat.subprocess.run = fat.subprocess.run, fake_run
        try:
            with self.assertRaises(fat.DispatchFailed) as cm:
                fat.dispatch_entry(R1, "p", {})
        finally:
            fat.subprocess.run = real
        self.assertIn("503", str(cm.exception))


class HelpTests(unittest.TestCase):
    def test_help_prints_usage_and_runs_nothing(self):
        r = subprocess.run([sys.executable, str(TOOL), "--help"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)
        self.assertIn("FA_DISPATCH_PREFS", r.stdout)

    def test_list_shows_the_order_without_dispatching(self):
        r = subprocess.run([sys.executable, str(TOOL), "--list"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60,
                           env=dict(os.environ, LA_API_KEYS_DIR="/nonexistent"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("Gemini 3.8 Flash", r.stdout)
        self.assertIn("no key", r.stdout)


if __name__ == "__main__":
    unittest.main()
