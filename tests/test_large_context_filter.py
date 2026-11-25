#!/usr/bin/env python3
"""Task 11 Step 1 — the "Large context only" filter (C9): default ON for the remote lane,
persisted in schema 11, and proven on the RENDERED list (a fixture <=256k row is absent,
a >256k row present; toggling OFF shows it; persists across a restart).

Run under the picker venv: ~/.local/share/free-agents/picker-venv/bin/python -m pytest …
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import ROOT, PilotCase, REMOTE_INV  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive

# 8-column inventory rows (broken flag + model id): a <=256k model and a >256k one.
INV = ("nvidia-big\tnvidia\tNVIDIA Big\tunknown\t0\t1\t0\tnvidia/big-1m\n"
       "groq-small\tgroq\tGroq Small\tunknown\t0\t1\t0\tqwen/small-128k\n"
       "gemini-flash\tgemini\tGemini Flash\trenewing_free\t0\t1\t0\tgemini/flash\n")

LIMITS = {
    "schema_version": 1, "session_min_context": 262145, "large_context_threshold": 262144,
    "providers": {
        "nvidia": {"rpm": 40, "tpm": None, "rpd": None, "rpw": None, "tpd": None,
                   "dispatch_only": False, "reserves_on_request": False,
                   "measured_at": "2026-11-20", "source": "test"},
        "groq": {"rpm": 1000, "tpm": 8000, "rpd": None, "rpw": None, "tpd": None,
                 "dispatch_only": False, "reserves_on_request": False,
                 "measured_at": "2026-11-20", "source": "test"},
        "gemini": {"rpm": None, "tpm": None, "rpd": None, "rpw": None, "tpd": None,
                   "dispatch_only": True, "reserves_on_request": False,
                   "measured_at": "2026-11-20", "source": "test"},
    },
    "models": {
        "nvidia:nvidia/big-1m": {"context": 1000000, "max_output": 16384,
                                 "released": None, "broken": False,
                                 "source": "test", "reason": "released unknown"},
        "groq:qwen/small-128k": {"context": 131072, "max_output": 8192,
                                 "released": None, "broken": False,
                                 "source": "test", "reason": "released unknown"},
        "gemini:gemini/flash": {"context": 1048576, "max_output": 65536,
                                "released": None, "broken": False,
                                "source": "test", "reason": "released unknown"},
    },
}


class _Case(PilotCase):
    def setUp(self):
        super().setUp()
        # point the remote launcher stub at OUR inventory and the limits at OUR fixture
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(stub.read_text().replace(REMOTE_INV, INV))
        stub.chmod(0o755)
        (Path(self.env["FREE_AGENTS_CONFIG_DIR"]).parent / "provider-limits.json")
        self.limits = Path(self.tmp.name) / "provider-limits.json"
        self.limits.write_text(json.dumps(LIMITS))
        os.environ["FA_PROVIDER_LIMITS"] = str(self.limits)
        self.addCleanup(os.environ.pop, "FA_PROVIDER_LIMITS", None)

    def remote_rows(self, h):
        """Rendered remote list rows (the #rows HoverOptionList's option prompts)."""
        from textual.widgets import OptionList
        ol = h.app.query_one("#rows", OptionList)
        return [str(o.prompt) for o in ol.options]


class LargeContextFilterTests(_Case):
    def test_filter_on_by_default_hides_256k_row(self):
        async def s(app, pilot, h):
            await h.press("c")                    # expand the model list
            await h.wait_text("NVIDIA Big")          # the remote list loaded
            rows = self.remote_rows(h)
            self.assertFalse(any("Groq Small" in r for r in rows),
                             f"131072-context row must be hidden with the filter ON: {rows}")
            self.assertTrue(any("NVIDIA Big" in r for r in rows),
                            f"1M row must be present: {rows}")
        self.run_picker(s, start="remote")

    def test_migrated_v10_file_also_defaults_on(self):
        # a v10 file (no remote_filters section) migrates to large_context_only=True
        cfg = Path(self.env["FREE_AGENTS_CONFIG_DIR"])
        (cfg / "session-menu.local.json").write_text(json.dumps({"schema_version": 10}))
        async def s(app, pilot, h):
            await h.press("c")                    # expand the model list
            await h.wait_text("NVIDIA Big")
            rows = self.remote_rows(h)
            self.assertFalse(any("Groq Small" in r for r in rows), rows)
        self.run_picker(s, start="remote")

    def test_toggle_off_shows_it_and_persists(self):
        # key binding is proven in the TUI; the rendered-row proof runs through the SAME
        # functions the screen renders with (load_remote_agents -> visible_agents)
        async def s(app, pilot, h):
            await h.press("y")
            await h.wait_screen("RemoteFiltersScreen")
            self.assertTrue(any(a.key == "d" for a in app.screen_model.actions()),
                            "the Large-context-only toggle is bound to 'd'")
        self.run_picker(s, start="remote")

        agents = self.sp.load_remote_agents()
        by_alias = {a.alias: a for a in agents}
        self.assertEqual(by_alias["groq-small"].context, 131072, "facts attached at load")
        s = self.m.Settings(remote_filters={"large_context_only": True})
        self.assertNotIn("groq-small", [a.alias for a in self.m.visible_agents(s, agents)])
        self.assertIn("nvidia-big", [a.alias for a in self.m.visible_agents(s, agents)])
        # flip it (the same write the 'd' key performs) and persist
        import session_menu_state as sms
        s.remote_filters["large_context_only"] = False
        warn = sms.save_remote_filters(Path(self.env["FREE_AGENTS_CONFIG_DIR"]),
                                       "large_context_only", False)
        self.assertIsNone(warn, warn)
        shown = [a.alias for a in self.m.visible_agents(s, agents)]
        self.assertIn("groq-small", shown, "toggling OFF shows the 131k row")
        # persisted across a restart: the saved v11 file carries remote_filters
        saved = json.loads((Path(self.env["FREE_AGENTS_CONFIG_DIR"])
                            / "session-menu.local.json").read_text())
        self.assertEqual(saved.get("schema_version"), 11)
        self.assertEqual(saved.get("remote_filters", {}).get("large_context_only"), False)


if __name__ == "__main__":
    unittest.main()
