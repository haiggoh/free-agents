"""tests/test_picker_core.py — nicegui plan Task 6: picker_core holds the non-terminal core
(settings build, Savers, build_screen, loaders, command_for) so both frontends (Textual app and
NiceGUI adapter) drive the same code without importing the Textual app."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_menu_state as sms  # noqa: E402
import session_picker_model as m  # noqa: E402
import picker_core as pc  # noqa: E402


class BuildSettings(unittest.TestCase):
    def test_reads_store_and_env(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            sms.save_effort(cfg, "local_session", "high")
            savers = pc.Savers(cfg)
            with mock.patch.dict(os.environ, {"CSL_TELEMETRY": "1"}):
                s = pc.build_settings(None, savers, env=dict(os.environ))
            self.assertIs(s.telemetry, True, "CSL_TELEMETRY=1 must land in Settings")
            self.assertEqual(s.local_effort, "high", "the store's effort must be read")


class RemoteFiltersSaveThroughSavers(unittest.TestCase):
    def test_large_context_toggle_lands_in_the_savers_dir_not_repo_config(self):
        # main 0.62.0 C9 wrote remote_filters straight to sms.REPO_CONFIG_DIR, the only setting
        # that bypassed the injected saver: with LA_SESSION_MENU_CONFIG_DIR (GUI, tests) the
        # read and the write landed in DIFFERENT dirs and the toggle never stuck
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
            cfg = Path(d)
            with mock.patch.object(sms, "REPO_CONFIG_DIR", Path(repo)):
                s = pc.build_settings(None, pc.Savers(cfg))
                scr = pc.build_screen(m.Nav("remote_filters", m.HOME_OWNED), s, {}).screen
                row = next(a for a in scr.actions() if a.key == "d")
                self.assertTrue(row.put, "the Large-context row must be a typed setting")
                row.put(False)
                self.assertIs(sms.load(cfg).remote_filters.get("large_context_only"), False,
                              "toggle not in the savers dir")
                # csl's route (Enter -> run()): main 0.62.0-0.63.2 raised AttributeError here
                # ('RemoteFiltersScreen' has no '_regroup') right after saving
                row.run()
            stored = sms.load(cfg).remote_filters
            self.assertIs(stored.get("large_context_only"), True, "run() must toggle it back on")
            self.assertEqual(list(Path(repo).iterdir()), [], "REPO_CONFIG_DIR must stay untouched")


class SaversMerge(unittest.TestCase):
    def test_two_frontends_field_level_merge(self):
        with tempfile.TemporaryDirectory() as d:
            a = pc.Savers(Path(d))
            b = pc.Savers(Path(d))           # a second frontend on the SAME dir (possibly stale)
            a.effort("local_session", "high")
            b.temperature("local_session", "0.3")
            doc = json.loads((Path(d) / "session-menu.local.json").read_text())
            self.assertEqual(doc["effort"]["local_session"], "high",
                             "A's effort save must survive B's temperature save")
            self.assertEqual(doc["temperature"]["local_session"], "0.3")


class BuildScreen(unittest.TestCase):
    TARGETS = ["home", "about", "local", "remote", "lowkey", "download", "rate_limiter",
               "memguard", "runtime_manager", "runtime", "runtime_launchd", "api_keys",
               "runtime_versions:promote", "remote_filters", "cloud_config", "theme_config"]

    def test_covers_every_target(self):
        s = m.Settings()
        cache = {"local": [m.LocalModel("qwen-3.8-operator", family="qwen")],
                 "remote": [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown",
                                          model_id="nvidia-a")]}
        for t in self.TARGETS:
            with self.subTest(target=t):
                b = pc.build_screen(m.Nav(t, m.HOME_OWNED), s, dict(cache))
                self.assertIsNotNone(b.screen, t)
                self.assertIsInstance(b.prefetch, tuple)

    def test_prefetch_kinds(self):
        s = m.Settings()
        self.assertEqual(pc.build_screen(m.Nav("download", m.HOME_OWNED), s, {}).prefetch,
                         ("catalog",))
        b = pc.build_screen(m.Nav("runtime_versions:promote", m.HOME_OWNED), s, {})
        self.assertEqual(b.prefetch, (b.screen.inventory_kind,))


class CommandFor(unittest.TestCase):
    def test_lanes_unchanged(self):
        env = {"CSL_SELF": "/stub/csl", "CSL_REMOTE_LAUNCHER": "/stub/remote-session.sh"}
        with mock.patch.dict(os.environ, env, clear=False):
            local = pc.command_for(m.LaunchRequest("local", ["local", "--model", "x"], {}, "x"))
            self.assertEqual(local[0], "/stub/csl")
            self.assertIn("--picker-launch", local)
            self.assertIn("--model", local)
            remote = pc.command_for(m.LaunchRequest("remote", ["remote", "-a", "nvidia-a"], {}, "nvidia-a"))
            self.assertEqual(remote[0], "/stub/remote-session.sh")
            self.assertIn("-a", remote)
            self.assertIn("nvidia-a", remote)
            lowkey = pc.command_for(m.LaunchRequest("lowkey", ["lowkey", "--model", "x"], {}, "x"))
            self.assertTrue(lowkey[1].endswith("lowkey-cli.py"))
            self.assertIn("--model", lowkey)


if __name__ == "__main__":
    unittest.main()
