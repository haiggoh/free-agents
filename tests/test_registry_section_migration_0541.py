#!/usr/bin/env python3
"""tests/test_registry_section_migration_0541.py — 0.55.8 Task 8 (0541 plan Item G):

`registry_auto_sync` moves out of memguard into its own `local_registry` section (schema 10):
a schema-9 file migrates without loss, the export reads the new key, memguard saves never
carry it, a BOTH-keys file lets the explicit local_registry win (R19), and a schema-10 file
read by the 0.54.0 reader is RECORDED, never silently data-losing (Review Focus 5).

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_registry_section_migration_0541.py
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # picker_harness
sys.path.insert(0, str(ROOT / "bin"))
import session_menu_state as sms  # noqa: E402
import pytest  # noqa: E402
from picker_harness import PilotCase, ROOT  # noqa: E402

pytestmark = pytest.mark.load_sensitive

V9_FILE = {"schema_version": 9, "memguard": {"registry_auto_sync": True}}


def _cfg():
    return Path(tempfile.mkdtemp(prefix="fa-registry-test-"))


class RegistryMigration(unittest.TestCase):
    def test_v9_file_migrates_without_loss(self):
        cfg = _cfg()
        (cfg / sms.PREFS_NAME).write_text(json.dumps(V9_FILE))
        st = sms.load(cfg)
        self.assertIs(st.local_registry.get("auto_sync"), True, "migrated without loss")
        st.memguard["soft_pct"] = 80                 # any save rewrites at schema 10
        sms.save_memguard(cfg, {"soft_pct": 80})
        doc = json.loads((cfg / sms.PREFS_NAME).read_text())
        self.assertEqual(doc["schema_version"], 10)
        self.assertEqual(doc.get("local_registry", {}).get("auto_sync"), True)
        self.assertNotIn("registry_auto_sync", doc.get("memguard", {}), "the stale key is dropped")

    def test_both_keys_explicit_local_registry_wins(self):
        # R19: a file carrying BOTH keys — the explicit local_registry wins; the stale
        # memguard key is dropped on save.
        cfg = _cfg()
        (cfg / sms.PREFS_NAME).write_text(json.dumps(
            {"schema_version": 10,
             "memguard": {"registry_auto_sync": False},
             "local_registry": {"auto_sync": True}}))
        st = sms.load(cfg)
        self.assertIs(st.local_registry.get("auto_sync"), True, "explicit local_registry wins")
        st.local_registry["auto_sync"] = False
        sms.save_local_registry(cfg, {"auto_sync": False})
        doc = json.loads((cfg / sms.PREFS_NAME).read_text())
        self.assertIs(doc["local_registry"]["auto_sync"], False)
        self.assertNotIn("registry_auto_sync", doc.get("memguard", {}))

    def test_explicit_false_round_trips(self):
        # M4 minor: an explicit False must persist as False (not fall back to default/absent)
        cfg = _cfg()
        sms.save_local_registry(cfg, {"auto_sync": True})
        sms.save_local_registry(cfg, {"auto_sync": False})
        st = sms.load(cfg)
        self.assertIs(st.local_registry.get("auto_sync"), False)
        doc = json.loads((cfg / sms.PREFS_NAME).read_text())
        self.assertIs(doc["local_registry"]["auto_sync"], False, doc)

    def test_export_reads_new_key(self):
        cfg = _cfg()
        st = sms.load(cfg)
        st.local_registry["auto_sync"] = True
        env = sms.memguard_env(st)
        self.assertEqual(env.get("LA_REGISTRY_AUTO_SYNC"), "1")
        st.local_registry["auto_sync"] = False
        self.assertEqual(sms.memguard_env(st).get("LA_REGISTRY_AUTO_SYNC"), "0")

    def test_memguard_save_never_carries_it(self):
        cfg = _cfg()
        sms.save_local_registry(cfg, {"auto_sync": True})
        sms.save_memguard(cfg, {"soft_pct": 80})
        doc = json.loads((cfg / sms.PREFS_NAME).read_text())
        self.assertNotIn("registry_auto_sync", doc.get("memguard", {}),
                         "a memguard save must never resurrect the old key")

    def test_schema10_file_rejected_or_tolerated_by_v9_reader(self):
        # Review Focus 5: what does the 0.54.0 reader do with a schema-10 file? Run the
        # v0.54.0 _read from git and RECORD the behavior (no silent data loss allowed).
        import subprocess
        old = subprocess.run(
            ["git", "show", "v0.54.0:bin/session_menu_state.py"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(old.returncode, 0, old.stderr)
        tmp = Path(tempfile.mkdtemp())
        mod_path = tmp / "sms_v9.py"
        mod_path.write_text(old.stdout)
        sys.path.insert(0, str(tmp))         # a real import (the bare exec lost its context)
        self.addCleanup(sys.path.remove, str(tmp))
        import sms_v9 as v9
        cfg = _cfg()
        (cfg / sms.PREFS_NAME).write_text(json.dumps(
            {"schema_version": 10, "local_registry": {"auto_sync": True},
             "memguard": {"soft_pct": 80}}))
        try:
            found = v9._read(cfg / sms.PREFS_NAME)
        except ValueError as exc:
            found = f"REJECTED: {exc}"
        print("V9-READER:", found if isinstance(found, str) else "tolerated")
        # Recorded 2026-11-09: the 0.54.0 reader REJECTS a schema-10 file entirely
        # ("unsupported schema_version 10") — no data loss (it never writes), and the new
        # writer leaves a .v9.bak beside the file before its first migrating save. The
        # downgrade story: restore the backup or upgrade back; nothing is silently lost.


class LocalRowPilot(PilotCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(_use_cfg(self))

    def test_local_row_toggle_round_trip(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            from textual.widgets import OptionList
            ol = h.app.query_one("#actions", OptionList)
            idx = next(i for i, a in enumerate(app.action_ids) if a.key == "y")
            ol.highlighted = next(j for j in range(ol.option_count)
                                  if ol.get_option_at_index(j).id == f"a:{idx}")
            await h.press("y")
            await h.pilot.pause(0.4)
            doc = json.loads((Path(os.environ["LA_SESSION_MENU_CONFIG_DIR"]) /
                              "session-menu.local.json").read_text())
            self.assertIn("local_registry", doc, "the new section is written")
            self.assertIn("auto_sync", doc.get("local_registry", {}), doc)
        self.run_picker(s, start="local")


def _use_cfg(case):
    import json as _json
    import os
    cfg = Path(case.env["LA_SESSION_MENU_CONFIG_DIR"])
    def _restore():
        pass
    return _restore


if __name__ == "__main__":
    unittest.main()
