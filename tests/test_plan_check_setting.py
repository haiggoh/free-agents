#!/usr/bin/env python3
"""Task 1 (plan-route-guard): the plan_check setting — FA_PLAN_CHECK in settings.json env,
plan_check()/save_plan_check()/PLAN_CHECK_LABELS in bin/cloud_session_env.py, the CLI
`set plan_check 0|1` / `show` line, and the CloudConfigScreen toggle row.

Every test runs against a throwaway CLAUDE_CONFIG_DIR; the real ~/.claude is never touched.
RED before the implementation lands in cloud_session_env.py / session_picker_model.py.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import cloud_session_env as cse  # noqa: E402
import session_picker_model as m  # noqa: E402


class PlanCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / ".claude"
        self.cfg.mkdir()
        self._old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._old

    def _stored_env(self):
        p = self.cfg / "settings.json"
        if not p.exists():
            return {}
        doc = json.loads(p.read_text())
        return doc.get("env", {}) if isinstance(doc, dict) else {}

    def test_default_is_on(self):
        self.assertTrue(cse.plan_check())

    def test_zero_is_off(self):
        cse.save_plan_check(False)
        self.assertFalse(cse.plan_check())
        self.assertEqual(self._stored_env().get("FA_PLAN_CHECK"), "0")

    def test_save_roundtrip_writes_only_its_key(self):
        (self.cfg / "settings.json").write_text(
            json.dumps({"env": {"OTHER_KEY": "keep", "FA_PLAN_CHECK": "1"}}))
        cse.save_plan_check(False)
        env = self._stored_env()
        self.assertEqual(env.get("OTHER_KEY"), "keep", "other env keys must survive")
        self.assertEqual(env.get("FA_PLAN_CHECK"), "0")
        cse.save_plan_check(True)
        self.assertEqual(self._stored_env().get("FA_PLAN_CHECK"), "1")
        self.assertTrue(cse.plan_check())

    def test_cli_set_and_show(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cse.main(["set", "plan_check", "0"])
        self.assertEqual(rc, 0)
        self.assertFalse(cse.plan_check())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cse.main(["show"])
        self.assertEqual(rc, 0)
        self.assertIn("plan_check = OFF", buf.getvalue())

    def test_cli_rejects_bad_value(self):
        with self.assertRaises(SystemExit) as cm:
            cse.main(["set", "plan_check", "2"])
        self.assertNotEqual(cm.exception.code, 0)

    def test_picker_row_present_and_toggles(self):
        s = m.Settings()
        cloud = m.CloudConfigScreen(s)
        row = next((a for a in cloud.actions() if "Plan routing check" in a.label), None)
        self.assertIsNotNone(row, "CloudConfigScreen must offer a 'Plan routing check' row")
        self.assertTrue(cse.plan_check())
        row.step(+1)
        self.assertFalse(cse.plan_check(), "toggling the row must persist OFF")
        self.assertEqual(self._stored_env().get("FA_PLAN_CHECK"), "0")
        row.step(+1)
        self.assertTrue(cse.plan_check())
        self.assertEqual(self._stored_env().get("FA_PLAN_CHECK"), "1")


if __name__ == "__main__":
    unittest.main()
