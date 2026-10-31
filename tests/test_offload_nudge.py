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


def nudge(base_url, **extra):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_BASE_URL"}
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
            for word in ("Agent", "Workflow", "fan-out", "ANNOUNCE", "/plug/bin/free-agent-tool.py",
                         "STATELESS", "SESSION", "Gemini 3.8 Flash"):
                self.assertIn(word, text, word)

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
        e = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_BASE_URL"}
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
        self.assertIn("one native agent call", text.lower())


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
