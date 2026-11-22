#!/usr/bin/env python3
"""Task 8 Step 1 — the effort resolver's precedence table and delivery (C6). RED first.

Precedence: explicit > saved lane default (present in the FILE) > per-alias last-launch >
provider default. Plus delivery: remote-session.sh --dry-run prints the saved lane default,
launch-claude-agent.sh's dry path picks local_session, and the dispatch envelope records
the resolved effort + provenance for a remote entry.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
_spec = importlib.util.spec_from_file_location("effort_resolve", REPO / "bin" / "effort_resolve.py")
er = importlib.util.module_from_spec(_spec)
sys.modules["effort_resolve"] = er
_spec.loader.exec_module(er)


class PrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "menu"
        self.cfg.mkdir()
        self.last = Path(self.tmp.name) / "last.json"
        os.environ["LA_LAST_EFFORT_FILE"] = str(self.last)
        self.addCleanup(os.environ.pop, "LA_LAST_EFFORT_FILE", None)

    def _save_menu(self, effort):
        (self.cfg / "session-menu.local.json").write_text(json.dumps(
            {"schema_version": 11, "effort": effort}))

    def _set_last(self, alias, level):
        (self.last).write_text(json.dumps({alias: level}))

    def test_explicit_wins_over_everything(self):
        self._save_menu({"remote_api_session": "max"})
        self._set_last("a", "low")
        self.assertEqual(er.resolve("remote_api_session", alias="a", explicit="high",
                                     config_dir=self.cfg), ("high", "explicit"))

    def test_saved_lane_beats_last_launch(self):
        self._save_menu({"remote_api_session": "max"})
        self._set_last("a", "low")
        self.assertEqual(er.resolve("remote_api_session", alias="a", config_dir=self.cfg),
                         ("max", "saved-lane"))

    def test_unsaved_lane_falls_through_to_last_launch(self):
        self._save_menu({"local_session": "high"})     # remote lane key ABSENT from the file
        self._set_last("a", "low")
        self.assertEqual(er.resolve("remote_api_session", alias="a", config_dir=self.cfg),
                         ("low", "last-launch"))

    def test_nothing_saved_gives_provider_default(self):
        self._save_menu({"local_session": "high"})
        self.assertEqual(er.resolve("remote_api_session", alias="a", config_dir=self.cfg),
                         (None, "provider-default"))

    def test_all_three_lanes_resolve(self):
        self._save_menu({"local_session": "high", "remote_api_session": "max", "lowkey": "xhigh"})
        for lane, want in (("local_session", "high"), ("remote_api_session", "max"),
                           ("lowkey", "xhigh")):
            with self.subTest(lane=lane):
                self.assertEqual(er.resolve(lane, config_dir=self.cfg), (want, "saved-lane"))

    def test_invalid_explicit_aborts_naming_it(self):
        with self.assertRaises(ValueError) as cm:
            er.resolve("remote_api_session", explicit="ultra")
        self.assertIn("ultra", str(cm.exception))

    def test_unknown_lane_aborts(self):
        with self.assertRaises(ValueError):
            er.resolve("nope")

    def test_cli_prints_level_and_provenance(self):
        self._save_menu({"remote_api_session": "max"})
        out = subprocess.run([sys.executable, str(REPO / "bin" / "effort_resolve.py"),
                              "resolve", "--lane", "remote_api_session",
                              "--config-dir", str(self.cfg)],
                             capture_output=True, text=True, env={**os.environ})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "max saved-lane")


class DeliveryTests(unittest.TestCase):
    """The launchers actually consume the resolver."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for d in ('bin', 'config', 'keys', 'home'):
            (self.root / d).mkdir()
        for rel in ('bin/remote-session.sh', 'bin/remote-provider-registry.sh',
                    'bin/remote-keys.sh', 'bin/blind-trust-settings.py',
                    'bin/launch-claude-agent.sh', 'bin/last-effort.py',
                    'bin/effort_resolve.py', 'config/remote-agents.sh',
                    'config/emoji.sh', 'config/dispatch-preferences.json',
                    'bin/session-fallbacks.py', 'config/broken-nvidia-models.json'):
            shutil.copy2(REPO / rel, self.root / rel)
        (self.root / 'keys/nvidia').write_text('fixture-not-a-real-key')
        self.cfgdir = self.root / 'menu'
        self.cfgdir.mkdir()
        (self.cfgdir / 'session-menu.local.json').write_text(json.dumps(
            {"schema_version": 11, "effort": {"remote_api_session": "max"}}))
        self.last = self.root / 'last.json'
        self.last.write_text(json.dumps({"nvidia-glm53": "low"}))   # last-launch low, saved max
        self.env = dict(os.environ, HOME=str(self.root / 'home'),
                        TMPDIR=str(self.root), LA_API_KEYS_DIR=str(self.root / 'keys'),
                        FREE_AGENTS_CONFIG_DIR=str(self.cfgdir),
                        LA_LAST_EFFORT_FILE=str(self.last),
                        LA_REMOTE_PROXY_PORT_MIN='47300', LA_REMOTE_PROXY_PORT_MAX='47400')

    def test_remote_dry_run_uses_saved_lane_default(self):
        r = subprocess.run(['bash', str(REPO / 'bin/remote-session.sh'), 'nvidia-glm53',
                            '--dry-run', '--effort', ''], capture_output=True, text=True,
                           env=self.env)
        # --dry-run prints the resolved EFFORT_CHOICE; the saved lane default (max) must win
        # over the per-alias last-launch (low)
        self.assertIn("max", r.stdout + r.stderr, r.stdout + r.stderr)

    def test_local_launcher_dry_path_picks_local_lane(self):
        (self.cfgdir / 'session-menu.local.json').write_text(json.dumps(
            {"schema_version": 11, "effort": {"remote_api_session": "max", "local_session": "xhigh"}}))
        r = subprocess.run(['bash', str(REPO / 'bin/launch-claude-agent.sh'), '--help'],
                           capture_output=True, text=True, env=self.env)
        # the dry/help path must resolve local_session (xhigh), not the remote lane (max)
        self.assertIn("xhigh", r.stdout + r.stderr, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
