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
        self.assertEqual(order[:6], ["gemini-3.8-flash", "nvidia-deepseek-v4.1-flash",
                                     "nvidia-glm-5.3", "nvidia-glm-5.3-flash", "nvidia-step5-preview",
                                     "nvidia-nemotron-3-ultra"])   # stepfun above nemotron (user 2026-10-24)
        self.assertEqual(order[6:], ["local-deepseek-v4-flash", "local-laguna-s", "local-kat-coder"])

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

        def fake_dispatch(entry, prompt, env_, thinking_effort=None):
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
        # Total-failure scenarios now ARM the session's paid-fallback mark: pin the mark dir
        # to a temp sandbox for EVERY run, or the test writes into the real
        # ~/.claude/free-agents/paid-fallback under the ambient CLAUDE_CODE_SESSION_ID
        # (measured 2026-10-24: this very test left a mark there).
        mark_tmp = tempfile.mkdtemp()
        old_mark = os.environ.get("FA_FALLBACK_MARK_DIR")
        os.environ["FA_FALLBACK_MARK_DIR"] = mark_tmp
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = fat.run(payload, env)
        finally:
            fat.dispatch_entry, fat.local_on_disk = real
            if old_mark is None:
                os.environ.pop("FA_FALLBACK_MARK_DIR", None)
            else:
                os.environ["FA_FALLBACK_MARK_DIR"] = old_mark
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


class SparseSetupTests(Base):
    """A user with ONE model family (one key, or one local model): avoid_family and role are
    preferences, so they degrade to what is there instead of failing (user 2026-10-23). Probed
    on 0.41.2: every case below exited 1 claiming 'no keys, no local weights'."""

    G = dict(R1, family="google", roles=["operator", "validator"])
    N = dict(R2, family="zhipu", roles=["reasoner"])
    K = dict(L1, family="kwaipilot", roles=["operator"])

    def run_sparse(self, payload, keys=(), local=(), order=None):
        for k in keys:
            self.key(k)
        p = Path(self.tmp.name) / "p.json"
        p.write_text(json.dumps(prefs(*(order or (self.G, self.N, self.K)))))
        env = dict(self.env, FA_DISPATCH_PREFS=str(p))
        out, err = io.StringIO(), io.StringIO()
        real = (fat.dispatch_entry, fat.local_on_disk)
        fat.dispatch_entry = lambda entry, prompt, env_, thinking_effort=None: f"answer from {entry['id']}"
        fat.local_on_disk = lambda a: a in local
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = fat.run(payload, env)
        finally:
            fat.dispatch_entry, fat.local_on_disk = real
        return code, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()

    def test_one_key_review_falls_back_to_the_same_family_and_recommends_another(self):
        code, res, err = self.run_sparse({"prompt": "review", "role": "validator", "avoid_family": "google"},
                                         keys=["gemini"])
        self.assertEqual(code, 0, err)
        self.assertEqual(res["model_used"], "r1")
        self.assertIs(res["family_avoided"], False)
        self.assertIn("same family", err)
        self.assertIn("API keys", err)                  # the recommendation names where to add one

    def test_one_local_model_avoiding_its_own_family_still_runs(self):
        code, res, err = self.run_sparse({"prompt": "review", "avoid_family": "kwaipilot"}, local=["loc-a"])
        self.assertEqual(code, 0, err)
        self.assertEqual(res["model_used"], "l1")
        self.assertIs(res["family_avoided"], False)

    def test_a_role_nobody_on_this_machine_fills_degrades_with_a_note(self):
        code, res, err = self.run_sparse({"prompt": "think", "role": "reasoner"}, local=["loc-a"])
        self.assertEqual(code, 0, err)
        self.assertEqual(res["model_used"], "l1")
        self.assertIs(res["role_matched"], False)
        self.assertIn("reasoner", err)

    def test_avoid_is_honoured_whenever_another_family_is_available(self):
        code, res, err = self.run_sparse({"prompt": "review", "avoid_family": "google"},
                                         keys=["gemini", "nvidia"])
        self.assertEqual(res["model_used"], "r2")
        self.assertIs(res["family_avoided"], True)
        self.assertNotIn("same family", err)

    def test_nothing_set_up_is_the_only_hard_failure_and_says_so(self):
        code, res, err = self.run_sparse({"prompt": "hi", "avoid_family": "google"})
        self.assertNotEqual(code, 0)
        self.assertIsNone(res)
        self.assertIn("no keys", err)


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

    def test_failure_reason_is_the_dispatchers_verdict_line_not_a_json_tail(self):
        # remote-agent-dispatch prints "✖ REMOTE ... failed: [...] HTTP 503 — <multi-line JSON>";
        # the LAST stderr line is the JSON's closing "]", which said nothing (measured 2026-10-21).
        err = ("REMOTE banner\n✖ REMOTE gemini failed: [transient] HTTP 503 — [{\n"
               '  "error": {"code": 503, "message": "high demand"}\n}\n]\n')

        def fake_run(cmd, **_kw):
            return subprocess.CompletedProcess(cmd, 3, stdout="", stderr=err)

        real, fat.subprocess.run = fat.subprocess.run, fake_run
        try:
            with self.assertRaises(fat.DispatchFailed) as cm:
                fat.dispatch_entry(R1, "p", {})
        finally:
            fat.subprocess.run = real
        self.assertIn("HTTP 503", str(cm.exception))
        self.assertNotEqual(str(cm.exception).strip(), "]")


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



class Exhaustion(unittest.TestCase):
    """Honest exhaustion (cloud-screen plan Task 4): only sent-and-failed attempts count; the
    paid escape arms at min_real_attempts across min_providers, or when everything available
    failed — and run() writes the session-scoped mark only then."""

    POLICY = {"min_real_attempts": 3, "min_providers": 2, "retry_after_max_s": 30,
              "wall_clock_cap_s": 600}

    @staticmethod
    def order(n):
        """n available remote entries: two nvidia, then gemini, then nvidia."""
        providers = ["nvidia", "nvidia", "gemini", "nvidia"][:n]
        return [{"id": f"e{i}", "kind": "remote", "provider": prov, "model": "m",
                 "display": f"E{i}", "smoke_tested": True} for i, prov in enumerate(providers)]

    def test_three_failures_on_one_provider_are_not_exhaustion(self):
        atts = [{"id": f"n{i}", "provider": "nvidia", "ok": False} for i in range(3)]
        self.assertFalse(fat.exhausted(atts, self.order(4), self.POLICY))  # one entry never tried

    def test_three_failures_across_two_providers_are_exhaustion(self):
        atts = [{"id": "n1", "provider": "nvidia", "ok": False},
                {"id": "n2", "provider": "nvidia", "ok": False},
                {"id": "g1", "provider": "gemini", "ok": False}]
        self.assertTrue(fat.exhausted(atts, self.order(3), self.POLICY))

    def test_skipped_entries_do_not_count(self):
        atts = [{"id": "n1", "provider": "nvidia", "ok": False},
                {"id": "n2", "provider": "nvidia", "ok": False}]
        self.assertFalse(fat.exhausted(atts, self.order(4), self.POLICY))

    def test_every_available_failed_counts_when_fewer_exist(self):
        self.assertTrue(fat.exhausted(
            [{"id": "g1", "provider": "gemini", "ok": False}], self.order(1), self.POLICY))

    def test_no_attempts_is_not_exhaustion(self):
        self.assertFalse(fat.exhausted([], self.order(2), self.POLICY))

    def run_main(self, payload, fake, monotonic=None, keys_dir=None):
        """Run fat.run with recorded dispatches; keys_dir makes entries available."""
        env = {"LA_API_KEYS_DIR": keys_dir or "missing"}
        old_env = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        calls = {"n": 0}

        def fake_dispatch(entry, prompt, e, thinking_effort=None):
            ok, text = fake.get(entry["id"], (False, "boom"))
            if ok:
                return text
            raise fat.DispatchFailed(text)

        def fake_clock():
            seq = monotonic or [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
            v = seq[min(calls["n"], len(seq) - 1)]
            calls["n"] += 1
            return v

        class FakeTime:
            monotonic = staticmethod(fake_clock)
            time = staticmethod(lambda: 0.0)

        real = (fat.dispatch_entry, fat.local_on_disk, fat.time)
        fat.dispatch_entry, fat.local_on_disk, fat.time = fake_dispatch, (lambda a: True), FakeTime
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = fat.run(payload, env)
        finally:
            fat.dispatch_entry, fat.local_on_disk, fat.time = real
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return code, out.getvalue(), err.getvalue()

    @staticmethod
    def last_json(err: str) -> dict:
        return json.loads([ln for ln in err.splitlines() if ln.startswith("{")][-1])

    def test_wall_clock_cap_stops_new_attempts(self):
        code, _out, err = self.run_main({"prompt": "hi"}, {}, monotonic=[0.0, 601.0, 602.0, 603.0])
        self.assertEqual(code, 1)
        res = self.last_json(err)
        self.assertEqual(len(res["attempts"]), 0, "the cap fires before any new attempt starts")
        self.assertIn("wall-clock cap", err)
        self.assertFalse(res["paid_fallback_allowed"])

    def test_total_failure_arms_the_mark_for_this_session(self):
        keys = tempfile.mkdtemp()
        Path(keys, "gemini").write_text("fixture-key")
        mark_dir = tempfile.mkdtemp()
        old_dir, old_sid = os.environ.get("FA_FALLBACK_MARK_DIR"), os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["FA_FALLBACK_MARK_DIR"] = mark_dir
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-arm-1"
        try:
            code, _out, err = self.run_main({"prompt": "hi"}, {}, keys_dir=keys)
            self.assertEqual(code, 1)
            res = self.last_json(err)
            self.assertTrue(res["paid_fallback_allowed"], "one key, one real failure = exhausted")
            sys.path.insert(0, str(ROOT / "bin"))
            import fa_fallback_mark as fm
            doc = fm.consume_mark("sess-arm-1")          # inside the sandboxed env
            self.assertIsNotNone(doc, "the mark is written when CLAUDE_CODE_SESSION_ID is set")
            self.assertIsNone(fm.consume_mark("sess-arm-1"), "single use")
        finally:
            if old_dir is None:
                os.environ.pop("FA_FALLBACK_MARK_DIR", None)
            else:
                os.environ["FA_FALLBACK_MARK_DIR"] = old_dir
            if old_sid is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = old_sid

    def test_total_failure_without_a_session_writes_no_mark(self):
        keys = tempfile.mkdtemp()
        Path(keys, "gemini").write_text("fixture-key")
        mark_dir = tempfile.mkdtemp()
        old_dir, old_sid = os.environ.get("FA_FALLBACK_MARK_DIR"), os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["FA_FALLBACK_MARK_DIR"] = mark_dir
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        try:
            code, _out, err = self.run_main({"prompt": "hi"}, {}, keys_dir=keys)
        finally:
            if old_dir is None:
                os.environ.pop("FA_FALLBACK_MARK_DIR", None)
            else:
                os.environ["FA_FALLBACK_MARK_DIR"] = old_dir
            if old_sid is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = old_sid
        self.assertEqual(self.last_json(err)["paid_fallback_allowed"], True)
        self.assertEqual(list(Path(mark_dir).iterdir()), [], "no session id, no mark")


class ThinkingPolicyTests(Base):
    """The dispatch thinking policy (waypoint fa-thinking-toggle): NO manual toggle for a
    stateless dispatch — a per-entry "thinking": on|off|auto field, role-derived defaults
    (reasoner/validator on, operator/utility off), model-constraint override applied LAST and
    reported in the result JSON. The council's cross-family validator is a validator, so the
    council rule (think ON where the model supports it) is preserved by the role default."""

    GLM = {"id": "g", "kind": "remote", "provider": "nvidia", "model": "z-ai/glm-5.3-flash",
           "display": "GLM Flash", "smoke_tested": True}
    NEMO = {"id": "n", "kind": "remote", "provider": "nvidia", "model": "nvidia/nemotron-3-ultra",
            "display": "Nemotron", "smoke_tested": True}

    def test_role_defaults(self):
        # Fails if the council rule breaks: validators and reasoners think by default.
        self.assertEqual(fat.resolve_thinking(dict(self.GLM), "validator"),
                         ("on", "high", False, None))
        self.assertEqual(fat.resolve_thinking(dict(self.GLM), "reasoner"),
                         ("on", "high", False, None))
        resolved, effort, overridden, _note = fat.resolve_thinking(dict(R1), "operator")
        self.assertEqual((resolved, overridden), ("off", False))
        self.assertIsNone(effort, "off on a non-GLM model sends NO reasoning_effort: don't ask")
        self.assertEqual(fat.resolve_thinking(dict(R1), "utility")[0], "off")
        # No role at all (general-purpose work) defaults off: dispatches are fire-and-forget.
        self.assertEqual(fat.resolve_thinking(dict(R1), None)[0], "off")

    def test_entry_field_wins_over_role_default(self):
        entry = dict(R1, thinking="on")
        self.assertEqual(fat.resolve_thinking(entry, "operator"), ("on", "high", False, None))
        entry = dict(R1, thinking="off")
        self.assertEqual(fat.resolve_thinking(entry, "validator")[0], "off")

    def test_glm_off_maps_to_the_measured_low_dial(self):
        # Fails if OFF claims honored-ness it does not have: GLM on NIM always runs its
        # thinking template (chat_template_kwargs inert, measured 2026-10-13) — reasoning_effort
        # low (≈0 reasoning tokens, verified direct) is the off-equivalent, and the note says so.
        resolved, effort, overridden, note = fat.resolve_thinking(dict(self.GLM), "operator")
        self.assertEqual((resolved, effort, overridden), ("off", "low", False))
        self.assertIn("always", note.lower())

    def test_glm_off_with_explicit_effort_is_the_override_case(self):
        # Fails if the role default silently beats the maintainer's per-model effort dial (or
        # vice versa): an explicit reasoning_effort is the more specific fact, it stands, and
        # the result must say the OFF choice was overridden by the model constraint.
        entry = dict(self.GLM, reasoning_effort="high")
        resolved, effort, overridden, note = fat.resolve_thinking(entry, "operator")
        self.assertEqual((resolved, effort, overridden), ("off", "high", True))
        self.assertIn("reasoning_effort", note)

    def test_nemotron_off_cannot_be_honored_by_a_dispatch(self):
        # Fails if OFF is claimed on Nemotron: it gates reasoning with enable_thinking inside
        # chat_template_kwargs, which remote-agent-dispatch.py cannot send — the constraint
        # overrides, loudly, in the result JSON.
        resolved, effort, overridden, note = fat.resolve_thinking(dict(self.NEMO), "operator")
        self.assertEqual(resolved, "off")
        self.assertIsNone(effort)
        self.assertIs(overridden, True)
        self.assertIn("enable_thinking", note)
        # ON is native on Nemotron: nothing to send, no override.
        self.assertEqual(fat.resolve_thinking(dict(self.NEMO), "validator"), ("on", None, False, None))

    def _run_real_dispatch(self, payload, entries, providers=("nvidia",)):
        """Run fat.run with REAL dispatch_entry and a faked subprocess.run, so the argv the
        dispatcher receives is captured and the result JSON is inspectable. Returns
        (code, stdout, stderr, [argv])."""
        keys = Path(self.tmp.name) / "keys"
        keys.mkdir(exist_ok=True)
        for p in providers:
            (keys / p).write_text("fixture-key")
        p_file = Path(self.tmp.name) / "prefs.json"
        p_file.write_text(json.dumps({"order": entries}))
        env = {"LA_API_KEYS_DIR": str(keys), "FA_DISPATCH_PREFS": str(p_file)}
        seen = []

        def fake_run(cmd, **_kw):
            seen.append(list(cmd))
            out = Path(cmd[cmd.index("--outdir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "output.txt").write_text("answer")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        real = fat.subprocess.run
        fat.subprocess.run = fake_run
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = fat.run(payload, env)
        finally:
            fat.subprocess.run = real
        return code, out.getvalue(), err.getvalue(), seen

    def test_run_reports_the_resolution_in_the_result_json(self):
        # Fails if the resolution happens but never reaches the caller: the JSON must carry
        # the resolved choice, the override flag and the constraint note, and the dispatcher
        # argv must carry the measured off-equivalent for an operator dispatch to GLM.
        code, out, _err, cmds = self._run_real_dispatch({"prompt": "hi", "role": "operator"},
                                                        [dict(self.GLM)])
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertEqual(res["thinking"], "off")
        self.assertIs(res["thinking_overridden"], False)
        self.assertIn("always", res["thinking_note"].lower())
        self.assertEqual(cmd_effort(cmds[0]), "low",
                         "operator dispatch to GLM sends the measured off-equivalent")

    def test_run_validator_dispatch_thinks_by_default(self):
        # Fails if the council rule (cross-family validator thinks) is lost in the policy.
        code, out, _err, cmds = self._run_real_dispatch({"prompt": "hi", "role": "validator"},
                                                        [dict(R1, thinking="auto")],
                                                        providers=("gemini",))
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertEqual(res["thinking"], "on")
        self.assertEqual(cmd_effort(cmds[0]), "high")

    def test_run_overridden_case_reports_true(self):
        # Fails if the model constraint beats the choice silently: overridden=true in the
        # JSON, the explicit dial stands in the argv, and stderr announces it.
        code, out, err, cmds = self._run_real_dispatch(
            {"prompt": "hi", "role": "operator"},
            [dict(self.GLM, reasoning_effort="high")])
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertIs(res["thinking_overridden"], True)
        self.assertEqual(cmd_effort(cmds[0]), "high", "the entry's explicit dial stands")
        self.assertIn("thinking", err, "an override is announced on stderr too")


def cmd_effort(cmd):
    return cmd[cmd.index("--reasoning-effort") + 1] if "--reasoning-effort" in cmd else None
