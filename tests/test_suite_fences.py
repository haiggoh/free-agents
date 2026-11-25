"""tests/test_suite_fences.py — the suite never writes the user's state or the checkout's config/.

Measured 2026-11-25: a full run created `config/remote-last-effort.local.json` ({"nvidia-glm53": "max"}) in
the checkout it ran in — in the CANONICAL checkout that is the user's live effort store — because
bin/last-effort.py defaults there when LA_LAST_EFFORT_FILE is unset and conftest set no fence. The
same gap existed for ~/.claude/free-agents/backend-upgrades.json (LA_BACKEND_UPGRADES_STATE), while
tests/test_cycle_ui_0554.py claimed a conftest tripwire that did not exist. conftest now forces both
to sandboxes (subprocesses inherit them) and trips any in-process write to the real upgrades file.
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_EFFORT_FILE = ROOT / "config" / "remote-last-effort.local.json"
REAL_UPGRADES = Path.home() / ".claude" / "free-agents" / "backend-upgrades.json"


def _load_backend_upgrades():
    spec = importlib.util.spec_from_file_location("backend_upgrades_fence_probe",
                                                  ROOT / "install" / "backend_upgrades.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LastEffortFence(unittest.TestCase):
    def test_conftest_points_the_effort_store_outside_the_checkout(self):
        fence = os.environ.get("LA_LAST_EFFORT_FILE")
        self.assertTrue(fence, "conftest must force LA_LAST_EFFORT_FILE (subprocesses inherit it)")
        self.assertNotEqual(Path(fence).resolve().parent, (ROOT / "config").resolve(),
                            "the fence must not be the checkout's own config/ dir")

    def test_a_subprocess_set_lands_in_the_fence_never_in_config(self):
        fence = os.environ.get("LA_LAST_EFFORT_FILE")
        # RED-safe: without the fence the subprocess would write config/ — fail before running it
        self.assertTrue(fence, "no LA_LAST_EFFORT_FILE fence; refusing to run last-effort.py unfenced")
        before = REPO_EFFORT_FILE.read_bytes() if REPO_EFFORT_FILE.exists() else None
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "last-effort.py"), "set",
                            "fence-probe-alias", "high"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        after = REPO_EFFORT_FILE.read_bytes() if REPO_EFFORT_FILE.exists() else None
        self.assertEqual(after, before, "last-effort.py wrote the checkout's config/ effort store")
        stored = json.loads(Path(str(fence)).read_text())
        self.assertEqual(stored.get("fence-probe-alias"), "high")


class BackendUpgradesFence(unittest.TestCase):
    def test_conftest_points_the_upgrade_state_at_a_sandbox(self):
        bu = _load_backend_upgrades()
        self.assertNotEqual(bu.state_path().resolve(), REAL_UPGRADES.resolve(),
                            "conftest must force LA_BACKEND_UPGRADES_STATE to a sandbox")

    def test_an_in_process_write_to_the_real_file_trips_before_writing(self):
        # RED-safe: only call the writer once the tripwire is known to be installed — without it
        # save_state() below would write the user's REAL file
        self.assertTrue(getattr(Path.write_text, "_fa_real_state_tripwire", False)
                        and getattr(Path.replace, "_fa_real_state_tripwire", False),
                        "conftest installs no tripwire on Path.write_text/Path.replace")
        bu = _load_backend_upgrades()
        before = REAL_UPGRADES.stat().st_mtime_ns if REAL_UPGRADES.exists() else None
        saved = os.environ.pop("LA_BACKEND_UPGRADES_STATE", None)
        try:
            with self.assertRaises(AssertionError) as cm:
                bu.save_state({"pending": [], "probe": "fence"})
        finally:
            if saved is not None:
                os.environ["LA_BACKEND_UPGRADES_STATE"] = saved
        self.assertIn("backend-upgrades", str(cm.exception))
        after = REAL_UPGRADES.stat().st_mtime_ns if REAL_UPGRADES.exists() else None
        self.assertEqual(after, before, "the tripwire must fire BEFORE the real file is touched")


if __name__ == "__main__":
    unittest.main()
