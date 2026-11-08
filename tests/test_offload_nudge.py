#!/usr/bin/env python3
"""hooks/offload-nudge.py: a PAID session is told free offloading is the default (fan-out included)
and to announce the model; a FREE session gets the council nudge (no cost reason, but cross-family review still pays).

Usage: python3 tests/test_offload_nudge.py [-v]
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "offload-nudge.py"


ROUTE_KEYS = ("FA_ROUTE_AGENT", "FA_ROUTE_WORKFLOW", "FA_REPLACE_AGENTS")


def nudge(base_url, **extra):
    # Scrub the machine's registered route state from the PROCESS env too: settings.json env
    # is applied to every session's environment, so a saved FA_ROUTE_AGENT would win over the
    # tests' defaults even with HOME isolated (route() merges registered state over the env).
    env = {k: v for k, v in os.environ.items()
           if k != "ANTHROPIC_BASE_URL" and k not in ROUTE_KEYS}
    if base_url is not None:
        env["ANTHROPIC_BASE_URL"] = base_url
    env.update(extra)
    env["CLAUDE_PLUGIN_ROOT"] = "/plug"
    # Isolate HOME: route() lets the registered settings.json override the process env by
    # design, so a saved FA_ROUTE_AGENT on the real machine would leak into the test.
    import tempfile
    env["HOME"] = tempfile.mkdtemp(prefix="nudge-home-")
    r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=10)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


class NudgeTests(unittest.TestCase):
    def test_paid_session_makes_free_offloading_the_default(self):
        for base in (None, "https://gateway.example.com"):
            text = nudge(base)
            self.assertIn("FREE OFFLOADING IS THE DEFAULT", text)
            # 0.54.0 (Task 9): the detailed routing now comes from the offload skill's marked
            # block (no hardcoded roster — "Gemini 3.8 Flash" must NOT appear any more)
            for word in ("Agent", "Workflow", "fan-out", "Announce the model",
                         "bin/free-agent-tool.py", "STATELESS", "SESSION"):
                self.assertIn(word, text, word)
            self.assertNotIn("Gemini 3.8 Flash", text)

    def test_free_session_gets_the_council_nudge_not_the_cost_one(self):
        # On a free session offloading saves no money, but a cross-family review still adds quality
        # (user 2026-10-23). The old local nudge told free sessions they were "on the paid model".
        for base in ("http://127.0.0.1:4143", "http://localhost:8000", "http://[::1]:4141"):
            text = nudge(base)
            self.assertNotIn("FREE OFFLOADING IS THE DEFAULT", text, base)
            self.assertNotIn("paid model", text, base)
            for word in ("saves no money", "DIFFERENT model family", "cross-check", '"avoid_family"',
                         '"files"', "/plug/bin/free-agent-tool.py", "Announce"):
                self.assertIn(word, text, word)
            self.assertNotIn("}}", text)


if __name__ == "__main__":
    unittest.main()


class RouteModeNudges(unittest.TestCase):
    """The nudge follows the Agent path's routing mode (0.43.0, cloud-screen plan Task 7):
    a paid path must never be told offloading is its default; free-only must say it never
    falls back; the free-first default keeps today's wording plus the honest-exhaustion
    sentence."""

    def nudge_with(self, **env):
        import tempfile
        e = {k: v for k, v in os.environ.items()
             if k != "ANTHROPIC_BASE_URL" and k not in ROUTE_KEYS}
        e.update(ANTHROPIC_BASE_URL="https://gateway.example.com", CLAUDE_PLUGIN_ROOT="/plug",
                 HOME=tempfile.mkdtemp(prefix="nudge-home-"), **env)
        r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=e,
                           stdin=subprocess.DEVNULL, timeout=10)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_paid_path_is_never_called_offload_by_default(self):
        text = self.nudge_with(FA_ROUTE_AGENT="paid")
        self.assertNotIn("FREE OFFLOADING IS THE DEFAULT", text)
        self.assertIn("only when you ask", text.lower())

    def test_free_only_path_says_no_fallback(self):
        text = self.nudge_with(FA_ROUTE_AGENT="free-only")
        self.assertIn("never falls back", text.lower())
        self.assertNotIn("FREE OFFLOADING IS THE DEFAULT", text)

    def test_free_first_keeps_wording_and_adds_the_fallback_sentence(self):
        text = self.nudge_with()
        self.assertIn("FREE OFFLOADING IS THE DEFAULT", text)
        self.assertIn("paid_fallback_allowed", text)
        # 0.54.0 (Task 9): the fallback sentence now comes from the skill's routing block;
        # 2026-11-08 route-watcher Task 2: it is the ONE stated-reason call, exhaustion alone
        # never permits paid
        self.assertIn("one stated-reason call", text.lower())
        self.assertIn("exhaustion alone never permits paid", text.lower())


class LaneBrandingTests(unittest.TestCase):
    """branding/credit-moments: the FREE nudge leads with the session's lane mark —
    exact prefix pins, never loosened to substrings. The paid nudge stays unbranded
    (a paid session's lane is ☁️, outside the About pair 📡🦾)."""

    PREFIX_TAIL = "free-agents: this session already runs on FREE inference"

    def test_remote_free_api_proxy_band_leads_with_satellite(self):
        # 4141-4151 = remote free-API proxy (same band as la-session-identity.sh)
        text = nudge("http://127.0.0.1:4143", LA_SESSION_KIND_EMOJI="")
        self.assertTrue(text.startswith(f"📡 {self.PREFIX_TAIL}"), text[:60])

    def test_local_inference_band_leads_with_robot(self):
        # 8000-8010 = local MLX inference
        text = nudge("http://localhost:8000", LA_SESSION_KIND_EMOJI="")
        self.assertTrue(text.startswith(f"🦾 {self.PREFIX_TAIL}"), text[:60])

    def test_launcher_env_beats_the_port_heuristic(self):
        text = nudge("http://localhost:4143", LA_SESSION_KIND_EMOJI="🦾")
        self.assertTrue(text.startswith(f"🦾 {self.PREFIX_TAIL}"), text[:60])

    def test_paid_nudge_stays_unbranded(self):
        text = nudge("https://gateway.example.com", LA_SESSION_KIND_EMOJI="☁️")
        self.assertFalse(text.startswith(("📡", "🦾", "☁️")), text[:60])
        self.assertTrue(text.startswith("free-agents is installed"), text[:60])


class GuardPrimerSection(unittest.TestCase):
    """guards_section (session-start guard primers): the SessionStart injection carries each
    PreToolUse guard's PRIMER once; the Agent/Workflow line follows the route modes and
    disappears when BOTH paths are paid — route=paid is the menu opt-out that turns the
    intercept hook off, so its primer text goes with it."""

    PRIMER_HOOKS = ("guard-own-endpoint.py", "guard-tool-owned-state.py", "read-pages-guard.py")

    @staticmethod
    def _primers():
        import importlib.util
        out = {}
        for name in GuardPrimerSection.PRIMER_HOOKS:
            spec = importlib.util.spec_from_file_location(
                name.replace("-", "_").removesuffix(".py"), HOOK.parent / name)
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            out[name] = mod.PRIMER
        return out

    @staticmethod
    def _mod():
        import importlib.util
        spec = importlib.util.spec_from_file_location("offload_nudge_under_test", HOOK)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def section(self, free=False, **modes):
        return self._mod().guards_section(modes or None, free)

    def test_three_guard_primers_present(self):
        text = self.section()
        for name, primer in self._primers().items():
            self.assertIn(primer, text, name)
        self.assertIn("guards", text)

    def test_route_matrix_names_only_non_paid_paths(self):
        for agent in ("paid", "free-first", "free-only"):
            for workflow in ("paid", "free-first", "free-only"):
                text = self.section(agent=agent, workflow=workflow)
                for name, primer in self._primers().items():
                    self.assertIn(primer, text, (agent, workflow, name))
                nonpaid = [p for p in ("agent", "workflow")
                           if {"agent": agent, "workflow": workflow}[p] != "paid"]
                agent_lines = [ln for ln in text.splitlines() if "intercepted on:" in ln]
                if nonpaid:
                    self.assertEqual(len(agent_lines), 1, (agent, workflow))
                    self.assertIn(f"intercepted on: {', '.join(nonpaid)} ", agent_lines[0])
                else:
                    self.assertEqual(agent_lines, [], (agent, workflow))

    def test_free_session_gets_guard_lines_but_no_agent_line(self):
        # free sessions pass through the intercept, but own-endpoint matters most there
        text = self.section(free=True)
        for primer in self._primers().values():
            self.assertIn(primer, text)
        self.assertNotIn("intercepted on:", text)

    def test_paid_nudge_returns_empty_when_both_paths_paid(self):
        self.assertEqual(self._mod().paid_nudge("/plug", {"agent": "paid", "workflow": "paid"}), "")

    def test_main_both_paid_silences_agent_part_but_keeps_guard_lines(self):
        text = nudge("https://gateway.example.com", FA_ROUTE_AGENT="paid", FA_ROUTE_WORKFLOW="paid")
        self.assertNotIn("only when you ask", text.lower())
        self.assertNotIn("intercepted on:", text)
        for primer in self._primers().values():
            self.assertIn(primer, text)

    def test_main_mixed_routing_names_workflow_only(self):
        text = nudge("https://gateway.example.com", FA_ROUTE_AGENT="paid", FA_ROUTE_WORKFLOW="free-first")
        self.assertIn("only when you ask", text.lower())  # mixed: the paid paragraph stays
        self.assertIn("intercepted on: workflow ", text)
        self.assertNotIn("intercepted on: agent", text)

    def test_main_free_session_keeps_council_nudge_and_guard_lines(self):
        text = nudge("http://127.0.0.1:4143")
        self.assertIn("saves no money", text)
        for primer in self._primers().values():
            self.assertIn(primer, text)

    def test_load_primer_accepts_plain_and_annotated_assignment(self):
        # a future PRIMER: str = "..." (AnnAssign) must not silently drop the primer
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp(prefix="primer-ast-"))
        (d / "plain-guard.py").write_text('PRIMER = "plain primer"\n')
        (d / "annotated-guard.py").write_text('PRIMER: str = "annotated primer"\n')
        (d / "no-primer.py").write_text('X = 1\n')
        mod = self._mod()
        self.assertEqual(mod._load_primer(d, "plain-guard.py"), "plain primer")
        self.assertEqual(mod._load_primer(d, "annotated-guard.py"), "annotated primer")
        self.assertEqual(mod._load_primer(d, "no-primer.py"), "")
        self.assertEqual(mod._load_primer(d, "missing.py"), "")
