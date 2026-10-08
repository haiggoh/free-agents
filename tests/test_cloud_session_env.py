#!/usr/bin/env python3
"""cloud_session_env: the security-guidance review mode, kept in Claude Code's own settings.json
`env` so it reaches EVERY cloud session — native Anthropic login, API key or an LLM gateway, any
launcher. Audit 2026-10-08: the Cloud Session Configuration screen reached no cloud session at all.

Every test runs against a throwaway CLAUDE_CONFIG_DIR; the real ~/.claude is never touched."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import cloud_session_env as cse  # noqa: E402
import session_picker_model as m  # noqa: E402

REGISTRY_WITH = {"version": 2, "plugins": {
    "security-guidance@claude-plugins-official": [{"scope": "user", "version": "2.0.8"}]}}


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name)
        (self.cfg / "plugins").mkdir()
        self._old = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_USE_BEDROCK",
                                                     "SECURITY_GUIDANCE_DISABLE")}
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)
        os.environ.pop("SECURITY_GUIDANCE_DISABLE", None)
        self.addCleanup(self._restore)
        self.settings = self.cfg / "settings.json"

    def _restore(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def write_settings(self, doc, mode=0o600):
        self.settings.write_text(json.dumps(doc, indent=2) + "\n")
        os.chmod(self.settings, mode)

    def install(self, enabled=True):
        (self.cfg / "plugins" / "installed_plugins.json").write_text(json.dumps(REGISTRY_WITH))
        doc = json.loads(self.settings.read_text()) if self.settings.exists() else {}
        doc.setdefault("enabledPlugins", {})["security-guidance@claude-plugins-official"] = enabled
        self.write_settings(doc)


class SettingsEnvTests(_Sandbox):
    def test_absent_settings_is_default_and_nothing_is_written_on_read(self):
        st = cse.load()
        self.assertEqual(st["security_review"], 0)
        self.assertFalse(self.settings.exists())

    def test_cheaper_and_off_edit_only_the_owned_keys(self):
        """Discriminant: every unrelated key — other env vars, hooks, permissions — survives."""
        self.write_settings({"env": {"DISABLE_AUTOUPDATER": "1"}, "permissions": {"deny": ["X"]},
                             "hooks": {"Stop": []}})
        cse.save(1)
        doc = json.loads(self.settings.read_text())
        self.assertEqual(doc["env"], {"DISABLE_AUTOUPDATER": "1", "SECURITY_REVIEW_MODEL": "claude-sonnet-4-6"})
        self.assertEqual(doc["permissions"], {"deny": ["X"]})
        self.assertIn("hooks", doc)
        cse.save(2)
        doc = json.loads(self.settings.read_text())
        self.assertEqual(doc["env"], {"DISABLE_AUTOUPDATER": "1", "ENABLE_CODE_SECURITY_REVIEW": "0"})
        cse.save(0)
        self.assertEqual(json.loads(self.settings.read_text())["env"], {"DISABLE_AUTOUPDATER": "1"})

    def test_default_on_a_settings_file_without_env_adds_no_env_key(self):
        self.write_settings({"model": "opus"})
        before = self.settings.read_text()
        cse.save(0)
        self.assertEqual(self.settings.read_text(), before)   # byte-identical: no-op

    def test_round_trip_and_custom_model_is_recognised_not_clobbered(self):
        cse.save(2)
        self.assertEqual(cse.load()["security_review"], 2)
        self.write_settings({"env": {"SECURITY_REVIEW_MODEL": "claude-haiku-4-5"}})
        st = cse.load()
        self.assertEqual((st["security_review"], st["custom_model"]), (-1, "claude-haiku-4-5"))
        self.assertIn("custom (claude-haiku-4-5)", cse.describe(st))

    def test_mode_is_kept_and_symlink_is_followed(self):
        real = self.cfg / "dotfiles-settings.json"
        real.write_text("{}\n")
        os.chmod(real, 0o640)
        self.settings.symlink_to(real)
        cse.save(1)
        self.assertTrue(self.settings.is_symlink(), "settings.json symlink was replaced by a file")
        self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o640)
        self.assertIn("SECURITY_REVIEW_MODEL", real.read_text())

    def test_invalid_value_writes_nothing(self):
        with self.assertRaises(ValueError):
            cse.save(7)
        self.assertFalse(self.settings.exists())

    def test_non_object_settings_is_refused_not_overwritten(self):
        self.settings.write_text("[1, 2]\n")
        with self.assertRaises(ValueError):
            cse.save(1)
        self.assertEqual(self.settings.read_text(), "[1, 2]\n")


class PluginGateTests(_Sandbox):
    def test_not_installed_is_not_offered(self):
        self.assertEqual(cse.plugin_status(), (False, "not installed"))
        self.assertFalse(cse.offered())

    def test_installed_and_enabled_is_offered(self):
        self.install(enabled=True)
        self.assertEqual(cse.plugin_status(), (True, "active"))
        self.assertTrue(cse.offered())

    def test_installed_but_disabled_is_not_offered(self):
        self.install(enabled=False)
        self.assertEqual(cse.plugin_status()[1], "installed but disabled")
        self.assertFalse(cse.offered())

    def test_plugin_kill_switch_counts_as_disabled(self):
        self.install(enabled=True)
        doc = json.loads(self.settings.read_text())
        doc["env"] = {"SECURITY_GUIDANCE_DISABLE": "1"}
        self.write_settings(doc)
        self.assertFalse(cse.plugin_status()[0])

    def test_a_leftover_override_stays_visible_so_it_can_be_cleared(self):
        """Plugin removed after we set 'off': hiding the option would hide live state."""
        cse.save(2)
        self.assertTrue(cse.offered())


class ProviderTests(_Sandbox):
    def test_third_party_provider_skips_the_bare_cheaper_id(self):
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
        self.assertFalse(cse.load()["cheaper_offered"])
        self.assertEqual(cse.next_state(0), 2)
        self.assertEqual(cse.next_state(2), 0)

    def test_first_party_cycles_through_all_three(self):
        self.assertEqual([cse.next_state(0), cse.next_state(1), cse.next_state(2)], [1, 2, 0])
        self.assertEqual(cse.next_state(-1), 0)   # custom -> back to default


class CloudScreenTests(_Sandbox):
    def test_security_review_is_hidden_when_plugin_absent(self):
        actions = {a.key for a in m.CloudConfigScreen(m.Settings()).actions()}
        self.assertNotIn("s", actions)

    def test_security_review_cycles_and_saves_when_plugin_active(self):
        self.install(enabled=True)
        saved = []
        s = m.Settings(security_review=0, security_review_offered=True,
                       on_cloud_saved=lambda d: saved.append(dict(d)))
        actions = {a.key: a for a in m.CloudConfigScreen(s).actions()}
        self.assertIn("default (opus-4-7)", actions["s"].label)
        actions["s"].run()
        self.assertEqual((s.security_review, saved[-1]), (1, {"security_review": 1}))
        label = {a.key: a for a in m.CloudConfigScreen(s).actions()}["s"].label
        self.assertIn("cheaper (sonnet-4-6)", label)

    def test_options_that_cannot_reach_a_cloud_session_say_so(self):
        actions = {a.key: a for a in m.CloudConfigScreen(m.Settings(security_review_offered=True)).actions()}
        for key in ("x", "y", "p"):
            self.assertIn(m.NOT_GATEWAY_NOTE, actions[key].label, key)
        self.assertNotIn(m.NOT_GATEWAY_NOTE, actions["s"].label)


if __name__ == "__main__":
    unittest.main()
