#!/usr/bin/env python3
"""tests/test_local_dispatch_fallback.py — 0.56.14 (user 2026-11-12): the auto-generated
LOCAL dispatch list from the csl registry, as fallback after the curated dispatch order,
plus the opt-in 'only local agents in local sessions' filter."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import importlib.util as _ilu  # noqa: E402
_spec = _ilu.spec_from_file_location("free_agent_tool", ROOT / "bin" / "free-agent-tool.py")  # dashed filename
fat = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(fat)

CFG = """\
# fixture live config
la_register gemma-4-26b subdir/gems serve=rapid tool=hermes reasoner=none think=false spoof=claude-x effort=high roles=reasoner,validator
la_register qwen3-8b subdir/qwen serve=mlx tool=hermes reasoner=none think=false spoof=claude-y effort=low
la_role operator qwen3-8b medium
"""


class LocalRegistryParseTests(unittest.TestCase):
    def test_roles_parsed_from_register_and_role_lines(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.local.sh"
            p.write_text(CFG)
            roles = fat.local_registry_roles({"LA_CONFIG_SOURCE": str(p)})
        self.assertEqual(roles["gemma-4-26b"], {"reasoner", "validator"})
        self.assertEqual(roles["qwen3-8b"], {"operator"})


class FallbackOrderTests(unittest.TestCase):
    def test_local_entries_ride_after_the_curated_order_and_skip_offdisk(self):
        prefs = {"order": [{"id": "gemini-3.8-flash", "kind": "remote", "provider": "gemini",
                            "model": "gemini-3.8-flash", "roles": ["validator"], "family": "google"}]}
        env = {"LA_CONFIG_SOURCE": "", "HOME": os.environ.get("HOME", "/tmp")}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.local.sh"
            p.write_text(CFG)
            env["LA_CONFIG_SOURCE"] = str(p)
            with mock.patch.object(fat, "local_on_disk", lambda a: a == "gemma-4-26b"), \
                 mock.patch.object(fat, "_has_key", lambda prov, e: True):
                order = fat.candidates(prefs, env, role="validator", local_fallback=True)
        ids = [e["id"] for e in order]
        self.assertEqual(ids[0], "gemini-3.8-flash", "curated list first")
        self.assertIn("local-gemma-4-26b", ids)
        self.assertGreater(ids.index("local-gemma-4-26b"), ids.index("gemini-3.8-flash"),
                           "local fallback AFTER the curated order")
        self.assertNotIn("local-qwen3-8b", ids, "off-disk alias is not offered")
        entry = next(e for e in order if e["id"] == "local-gemma-4-26b")
        self.assertEqual(entry["kind"], "local")
        self.assertEqual(entry["roles"], ["reasoner", "validator"])

    def test_opt_in_filters_remote_entries_in_local_sessions_only(self):
        prefs = {"order": [{"id": "gemini-3.8-flash", "kind": "remote", "provider": "gemini",
                            "model": "m", "family": "google"},
                           {"id": "local-x", "kind": "local", "alias": "x", "family": "local"}]}
        env_local = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8000", "HOME": os.environ.get("HOME", "/tmp")}
        env_remote = {"ANTHROPIC_BASE_URL": "https://gateway.example.com", "HOME": os.environ.get("HOME", "/tmp")}
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "sessions-settings.json"
            import session_menu_state as sms
            sms.save_local_registry(cfg, {"agents_local_only": True})
            env_local["CLAUDE_CONFIG_DIR"] = td
            env_remote["CLAUDE_CONFIG_DIR"] = td
            fixture = Path(td) / "config.local.sh"
            fixture.write_text(CFG)                     # no real machine aliases leak in
            env_local["LA_CONFIG_SOURCE"] = str(fixture)
            env_remote["LA_CONFIG_SOURCE"] = str(fixture)
            with mock.patch.object(fat, "local_on_disk", lambda a: True), \
                 mock.patch.object(fat, "_local_agents_only", lambda e: True):
                kinds = [e["kind"] for e in fat.candidates(prefs, env_local, local_fallback=True)]
                self.assertTrue(kinds and all(k == "local" for k in kinds),
                                f"local session + opt-in: only local entries, got {kinds}")
                self.assertIn("local-x", [e["id"] for e in fat.candidates(prefs, env_local, local_fallback=True)])
                kinds = [e["kind"] for e in fat.candidates(prefs, env_remote, local_fallback=True)]
                self.assertEqual(kinds[0], "remote")
                self.assertTrue(all(k == "local" for k in kinds[1:]),
                                f"remote session: curated first, local fallback after: {kinds}")


if __name__ == "__main__":
    unittest.main()
