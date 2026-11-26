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
import tempfile
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
    sys.modules[spec.name] = mod   # dataclass fields resolve cls.__module__ via sys.modules
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

    def test_sessionfinish_fails_the_run_when_the_repo_effort_file_changed(self):
        # R2 FAIL half: conftest.pytest_sessionfinish must set exitstatus=1 if the repo
        # effort file was CREATED during the run (file absent at import). Exercised
        # in-process against a per-test TMP path by monkeypatching the live conftest
        # module's _REPO_EFFORT_FILE/_REPO_EFFORT_BEFORE — never touch the real checkout
        # path from a test (another xdist worker's sessionfinish could observe it).
        # The live conftest plugin module: tests/ is not on sys.path, so `import conftest`
        # fails — find the already-loaded module by its file instead of re-executing it.
        cf = next((m for m in sys.modules.values()
                   if getattr(m, "__file__", None)
                   and Path(m.__file__).resolve() == (ROOT / "tests" / "conftest.py").resolve()),
                  None)
        self.assertIsNotNone(cf, "live conftest module not found in sys.modules")
        tmp = Path(tempfile.mkdtemp(prefix="fa-effort-fence-")) / "remote-last-effort.local.json"
        orig = (cf._REPO_EFFORT_FILE, cf._REPO_EFFORT_BEFORE, cf._REPO_EFFORT_EXISTED_AT_IMPORT)
        cf._REPO_EFFORT_FILE, cf._REPO_EFFORT_BEFORE, cf._REPO_EFFORT_EXISTED_AT_IMPORT = tmp, None, False
        try:
            class _S:
                exitstatus = 0
            s = _S()
            cf.pytest_sessionfinish(s, 0)
            self.assertEqual(s.exitstatus, 0, "control: unchanged file must not fail the run")

            tmp.write_text('{"probe": true}\n')
            s2 = _S()
            cf.pytest_sessionfinish(s2, 0)
            self.assertEqual(s2.exitstatus, 1,
                             "a repo effort file CREATED during the run must FAIL the session")

            # report-only branch: file existed at import (canonical checkout's live store)
            cf._REPO_EFFORT_BEFORE = tmp.read_bytes()
            cf._REPO_EFFORT_EXISTED_AT_IMPORT = True
            tmp.write_text('{"probe": "after"}\n')
            s3 = _S()
            cf.pytest_sessionfinish(s3, 0)
            self.assertEqual(s3.exitstatus, 0,
                             "a PRE-EXISTING live store changing mid-run is report-only, not a failure")
        finally:
            cf._REPO_EFFORT_FILE, cf._REPO_EFFORT_BEFORE, cf._REPO_EFFORT_EXISTED_AT_IMPORT = orig


class BackendUpgradesFence(unittest.TestCase):
    def test_conftest_points_the_upgrade_state_at_a_sandbox(self):
        bu = _load_backend_upgrades()
        self.assertNotEqual(bu.state_path().resolve(), REAL_UPGRADES.resolve(),
                            "conftest must force LA_BACKEND_UPGRADES_STATE to a sandbox")
        # positive control: with the fence env set, the real writer must succeed INTO the
        # fence file (the tripwire must not false-positive on the sandboxed path)
        bu.save_state({"probe": "ok"})
        stored = json.loads(Path(os.environ["LA_BACKEND_UPGRADES_STATE"]).read_text())
        self.assertEqual(stored.get("probe"), "ok",
                         "save_state must succeed into the fenced sandbox, not trip")

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
        # direct trips, independent of save_state's write order: the real .tmp sibling via
        # write_text, and a replace() whose TARGET is the real file — both must raise first
        with self.assertRaises(AssertionError) as cm2:
            REAL_UPGRADES.with_suffix(".tmp").write_text("{}")
        self.assertIn("backend-upgrades", str(cm2.exception))
        fd, src = tempfile.mkstemp(prefix="fa-trip-src-")
        os.close(fd)
        try:
            with self.assertRaises(AssertionError) as cm3:
                Path(src).replace(REAL_UPGRADES)
            self.assertIn("backend-upgrades", str(cm3.exception))
        finally:
            os.unlink(src)
        after2 = REAL_UPGRADES.stat().st_mtime_ns if REAL_UPGRADES.exists() else None
        self.assertEqual(after2, before, "direct .tmp/replace trips must also fire BEFORE any write")


if __name__ == "__main__":
    unittest.main()
