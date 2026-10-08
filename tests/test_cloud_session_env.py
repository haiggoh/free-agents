#!/usr/bin/env python3
"""cloud_session_env: the ONE file through which the picker's cloud settings reach a gateway
session. Audit 2026-10-08: the Cloud Session Configuration screen persisted nothing and no gateway
launcher read anything it set, so every toggle on it was inert for the sessions it is named after.
These tests pin the contract the gateway launcher (claude-cloud-lean) sources."""
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import cloud_session_env as cse  # noqa: E402
import session_picker_model as m  # noqa: E402


class CloudEnvFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name)
        self.file = self.cfg / cse.FILE_NAME

    def test_absent_file_is_the_default_and_nothing_is_written_on_read(self):
        self.assertEqual(cse.load(self.cfg), {"security_review": 0})
        self.assertFalse(self.file.exists())

    def test_default_writes_no_override_at_all(self):
        # Fails if "default" pins a model: then a plugin default change would be silently masked.
        cse.save(self.cfg, {"security_review": 0})
        body = self.file.read_text()
        self.assertNotIn("SECURITY_REVIEW_MODEL=", body)
        self.assertNotIn("ENABLE_CODE_SECURITY_REVIEW=", body)

    def test_cheaper_sets_only_the_model(self):
        cse.save(self.cfg, {"security_review": 1})
        env = cse.env_lines(self.cfg)
        self.assertIn("export SECURITY_REVIEW_MODEL=claude-sonnet-4-6", env)
        self.assertFalse(any("ENABLE_CODE_SECURITY_REVIEW" in line for line in env))

    def test_off_disables_reviews_and_sets_no_model(self):
        cse.save(self.cfg, {"security_review": 2})
        env = cse.env_lines(self.cfg)
        self.assertIn("export ENABLE_CODE_SECURITY_REVIEW=0", env)
        self.assertFalse(any("SECURITY_REVIEW_MODEL" in line for line in env))

    def test_round_trip_survives_restart(self):
        cse.save(self.cfg, {"security_review": 2})
        self.assertEqual(cse.load(self.cfg)["security_review"], 2)

    def test_file_is_private(self):
        cse.save(self.cfg, {"security_review": 1})
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)

    def test_unknown_key_or_value_is_never_written(self):
        # Discriminant: the file is SOURCED by a shell; anything outside the whitelist must not reach it.
        with self.assertRaises(ValueError):
            cse.save(self.cfg, {"security_review": 7})
        with self.assertRaises(ValueError):
            cse.save(self.cfg, {"PATH": "/tmp/evil"})
        self.assertFalse(self.file.exists())

    def test_hand_edited_garbage_is_ignored_not_trusted(self):
        self.file.write_text("# state: security_review=2\n# state: security_review=9\n"
                             "# state: PATH=1\nsecurity_review=1\n$(touch /tmp/x)\nexport FOO=bar\n")
        self.assertEqual(cse.load(self.cfg), {"security_review": 2})
        # and a rewrite from that state emits only whitelisted lines
        cse.save(self.cfg, cse.load(self.cfg))
        self.assertNotIn("FOO", self.file.read_text())
        self.assertNotIn("$(", self.file.read_text())

    def test_the_shell_sees_exactly_the_saved_values(self):
        """What claude-cloud-lean does: `. file` in a clean shell, then read the env."""
        cse.save(self.cfg, {"security_review": 1})
        out = subprocess.run(
            ["/bin/sh", "-c", f'. "{self.file}"; printf "%s|%s" '
             '"${SECURITY_REVIEW_MODEL-unset}" "${ENABLE_CODE_SECURITY_REVIEW-unset}"'],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, check=True)
        self.assertEqual(out.stdout, "claude-sonnet-4-6|unset")


class CloudScreenTests(unittest.TestCase):
    def test_security_review_cycles_and_calls_the_saver(self):
        saved = []
        s = m.Settings(on_cloud_saved=lambda d: saved.append(dict(d)))
        actions = {a.key: a for a in m.CloudConfigScreen(s).actions()}
        self.assertIn("s", actions)
        self.assertIn("default (opus-4-7)", actions["s"].label)
        actions["s"].run()
        self.assertEqual(s.security_review, 1)
        self.assertEqual(saved[-1], {"security_review": 1})
        label = {a.key: a for a in m.CloudConfigScreen(s).actions()}["s"].label
        self.assertIn("cheaper (sonnet-4-6)", label)
        actions["s"].run()
        actions["s"].run()
        self.assertEqual(s.security_review, 0)

    def test_options_that_cannot_reach_a_gateway_session_say_so(self):
        """Audit 2026-10-08: x/y/p only ever reach local/free-API launchers."""
        actions = {a.key: a for a in m.CloudConfigScreen(m.Settings()).actions()}
        for key in ("x", "y", "p"):
            self.assertIn("not gateway", actions[key].label, key)
        self.assertNotIn("not gateway", actions["s"].label)


if __name__ == "__main__":
    unittest.main()
