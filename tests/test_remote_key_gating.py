#!/usr/bin/env python3
"""The remote model list shows only providers with a saved key; with no key at all it shows one
instruction row that opens API key setup. Also: the API-keys screen lists every provider the key
wizard knows (Streamlake was missing from the picker's hand-copied table in 0.27.1–0.29.4)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import session_picker_model as m  # noqa: E402


def _agents(keyed: set[str]):
    rows = [("nvidia-a", "nvidia"), ("gemini-flash", "gemini"), ("sl-kat", "streamlake")]
    return [m.RemoteAgent(a, p, a, "renewing_free", has_key=p in keyed) for a, p in rows]


class RemoteKeyGating(unittest.TestCase):
    def test_only_keyed_providers_are_listed(self):
        scr = m.RemoteScreen(m.Settings(), agents=_agents({"nvidia"}))
        self.assertEqual([g.id for g in scr.accordion.groups], ["nvidia"])

    def test_no_key_at_all_shows_the_instruction_row(self):
        scr = m.RemoteScreen(m.Settings(), agents=_agents(set()))
        groups = scr.accordion.groups
        self.assertEqual([g.id for g in groups], ["__nokeys__"])
        self.assertIn("API key", groups[0].label)
        scr.accordion.selected_id = groups[0].items[0].id
        nav = scr.activate_selected()
        self.assertIsInstance(nav, m.Nav)
        self.assertEqual(nav.target, "api_keys")

    def test_keyed_but_filtered_is_not_the_no_key_message(self):
        # Every keyed model hidden by a filter (here: trial tier) is a filter state, not "no key".
        agents = [m.RemoteAgent("cb", "cerebras", "cb", "trial", has_key=True)]
        scr = m.RemoteScreen(m.Settings(include_trials=False), agents=agents)
        self.assertEqual(scr.accordion.groups, [])

    def test_a_no_key_model_cannot_be_launched(self):
        scr = m.RemoteScreen(m.Settings(), agents=_agents({"nvidia"}))
        scr.accordion.selected_id = "gemini-flash"
        self.assertIsNone(scr.activate_selected())


class ApiKeysScreenUsesTheWizardTable(unittest.TestCase):
    def test_every_wizard_provider_is_offered(self):
        wizard = {p.slug for p in m.KEY_PROVIDERS}
        screen = {p[0] for p in m.APIKeysScreen.PROVIDERS}
        self.assertEqual(screen, wizard)
        self.assertIn("streamlake", screen)

    def test_signup_urls_come_from_the_wizard(self):
        self.assertEqual(m.PROVIDER_SIGNUP_URLS, {p.slug: p.url for p in m.KEY_PROVIDERS})

    def test_status_reads_the_real_key_dir(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("LA_API_KEYS_DIR")
            os.environ["LA_API_KEYS_DIR"] = d
            try:
                self.assertFalse(m.provider_has_key("streamlake"))
                Path(d, "streamlake").write_text("x")
                self.assertTrue(m.provider_has_key("streamlake"))
                Path(d, "cloudflare").write_text("x")
                self.assertFalse(m.provider_has_key("cloudflare"), "cloudflare needs its account-id file too")
            finally:
                if old is None:
                    os.environ.pop("LA_API_KEYS_DIR", None)
                else:
                    os.environ["LA_API_KEYS_DIR"] = old


if __name__ == "__main__":
    unittest.main()
