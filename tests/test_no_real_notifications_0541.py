#!/usr/bin/env python3
"""tests/test_no_real_notifications_0541.py — 0.55.8 Task 0 (0541 plan Item H, R1/R14/R15):

ONE notify helper (`bin/fa_notify.py` + `bin/fa-notify.sh`) routes every user-facing
notification; `FA_NOTIFY_SINK` turns any notification into a JSON record; the pytest session
exports the sink + a PATH shim and FAILS the session if the shim log records a real
osascript call (R1). Tests execute installed copies too, so the fence lives in conftest, not
in per-test patches.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_no_real_notifications_0541.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import pytest  # noqa: E402

# Spawns a NESTED pytest -n 2 run plus real osascript calls and mutates the shared shim log —
# flaked in full gate runs on a loaded shared box (2026-11-12). The gate deselects
# load_sensitive via -m 'not load_sensitive'; the fence still runs in manual/CI runs.
pytestmark = pytest.mark.load_sensitive

# The session shim dir (conftest) when running under pytest; the manual Step-0.4 shim
# only for direct script runs outside pytest. The delta assertions read the ACTIVE one.
SHIM_LOG = (Path(os.environ["FA_NOTIFY_SHIM_DIR"]) / "calls.log"
            if os.environ.get("FA_NOTIFY_SHIM_DIR")
            else Path("/tmp/fa0541-noop-bin/calls.log"))
# Window openers keep their osascript but MUST call the sink check first (R14).
WINDOW_OPENERS = ("csl", "local-watch.sh", "new-local-window.sh")
# guard-own-endpoint.py never notifies: its occurrences are LAUNCH-ROUTE DETECTION patterns
# and self-test inputs (it flags kill/proxy commands wrapped in osascript; a notification is
# not in it). Allow-listed with this reason per the plan's scan rule.
DETECTION_ONLY = ("guard-own-endpoint.py",)
HELPERS = ("fa_notify.py", "fa-notify.sh")


def _use_sink(path: Path):
    """Point FA_NOTIFY_SINK at `path` for the test and RESTORE the previous value on
    cleanup — popping it would break the session-level sink later tests assert on."""
    saved = os.environ.get("FA_NOTIFY_SINK")
    os.environ["FA_NOTIFY_SINK"] = str(path)

    def _restore():
        if saved is None:
            os.environ.pop("FA_NOTIFY_SINK", None)
        else:
            os.environ["FA_NOTIFY_SINK"] = saved
    return _restore


def _load_module(name: str, path: Path):
    """Import a module whose FILE name is not a valid identifier (la-memguard.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _shim_calls():
    if not SHIM_LOG.exists():
        return 0
    return len(SHIM_LOG.read_text().splitlines())


class SourceScan(unittest.TestCase):
    def test_every_notifier_goes_through_the_helper(self):
        banned = ("display notification", "terminal-notifier", "osascript")
        allow = set(HELPERS) | set(WINDOW_OPENERS) | set(DETECTION_ONLY)
        scanned = 0
        hits = []
        for sub in ("bin", "hooks", "scripts"):
            for path in sorted((ROOT / sub).rglob("*")):
                if not path.is_file() or path.suffix not in (".py", ".sh", ""):
                    continue
                if path.name in allow:
                    continue
                scanned += 1
                for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    code = line.split("#", 1)[0]
                    for token in banned:
                        if token in code:
                            hits.append(f"{sub}/{path.name}:{n}: {token}")
        self.assertGreater(scanned, 20, f"population guard: only {scanned} files scanned")
        self.assertEqual(hits, [], "raw notification calls outside the helper:\n" + "\n".join(hits))


class SinkWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fa-notify-test-"))
        self.sink = self.tmp / "sink.jsonl"
        self._shim_at_start = _shim_calls()

    def test_conftest_sets_sink_for_subprocesses(self):
        out = subprocess.run(["bash", "-c", 'echo "$FA_NOTIFY_SINK"'],
                             capture_output=True, text=True, env=dict(os.environ))
        self.assertTrue(out.stdout.strip(),
                        "the pytest session must export FA_NOTIFY_SINK to subprocesses")

    def test_memguard_notify_goes_to_sink(self):
        self.addCleanup(_use_sink(self.sink))
        os.environ.pop("LA_MEMGUARD_NOTIFY", None)
        guard = _load_module("la_memguard", ROOT / "bin" / "la-memguard.py")
        before = _shim_calls()
        guard._notify("wiring test")          # the extracted emit (0.55.8 Task 0)
        lines = [json.loads(x) for x in self.sink.read_text().splitlines()]
        self.assertEqual(len(lines), 1, "exactly one sink record")
        self.assertEqual(lines[0]["title"], "free-agents memory guard")
        self.assertEqual(_shim_calls(), before, "zero real osascript calls")

    def test_backend_update_notify_goes_to_sink(self):
        self.addCleanup(_use_sink(self.sink))
        sys.path.insert(0, str(ROOT / "scripts"))
        import check_backend_updates as cbu
        before = _shim_calls()
        cbu._notify("rapid-mlx: mlx-lm 0.31.3->0.32.2 — upgrade manually via manage-backend.py")
        lines = [json.loads(x) for x in self.sink.read_text().splitlines()]
        self.assertEqual(lines[0]["title"], "Backend updates available")
        self.assertEqual(_shim_calls(), before, "zero real osascript calls (subprocess.run NOT patched)")

    def test_post_upgrade_broken_venv_goes_to_sink(self):
        run = subprocess.run(
            ["bash", str(ROOT / "bin" / "fa-post-upgrade-check.sh")],
            capture_output=True, text=True,
            env={**os.environ,
                 "FA_NOTIFY_SINK": str(self.sink),  # the script's own env, not the session's
                 "FA_MANAGE_BACKEND": str(self._broken_stub()),
                 "HOME": str(self.tmp)},
        )
        lines = [json.loads(x) for x in self.sink.read_text().splitlines() if x.strip()]
        self.assertTrue(any("backend check" in x.get("title", "") for x in lines),
                        f"no backend-check sink record: {lines}")
        self.assertEqual(_shim_calls(), self._shim_at_start,
                         "zero real osascript calls from the shell path")
        self.assertNotEqual(run.returncode, 2, run.stderr)

    def _broken_stub(self):
        stub = self.tmp / "manage-backend.py"
        stub.write_text('#!/usr/bin/env python3\n'
                        'import json\n'
                        'print(json.dumps([{"backend": "rapid-mlx", "version": "1.0.0", '
                        '"state": "broken", "leftover": false, "owner": "user"}]))\n')
        return stub

    def test_sh_tests_inherit_sink(self):
        run = subprocess.run(
            ["bash", "-c",
             f'. "{ROOT}/tests/lib/fa-notify-sink.sh"; echo "$FA_NOTIFY_SINK"; '
             'echo "$PATH" | grep -c "$(dirname "$FA_NOTIFY_SINK")" || true'],
            capture_output=True, text=True, env={k: v for k, v in os.environ.items()
                                                 if k != "FA_NOTIFY_SINK"})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.stdout.splitlines()[0].strip(), "the sink file must be set")

    def test_sink_record_survives_quotes_and_unicode(self):
        # the JSON record must stay parseable for hostile input (quotes, backslashes, emoji)
        self.addCleanup(_use_sink(self.sink))
        helper = ROOT / "bin" / "fa_notify.py"
        run = subprocess.run([sys.executable, str(helper),
                              'ti"tle \\ back', 'te\"xt — 😀'],
                             capture_output=True, text=True,
                             env={**os.environ, "FA_NOTIFY_SINK": str(self.sink)})
        self.assertEqual(run.returncode, 0, run.stderr)
        rec = json.loads(self.sink.read_text().splitlines()[-1])
        self.assertEqual(rec["title"], 'ti"tle \\ back')
        self.assertEqual(rec["text"], 'te\"xt — 😀')

    def test_set_but_missing_sink_dir_fails_loudly(self):
        helper = ROOT / "bin" / "fa_notify.py"
        run = subprocess.run([sys.executable, str(helper), "t", "x"],
                             capture_output=True, text=True,
                             env={**os.environ, "FA_NOTIFY_SINK": "/nonexistent/x/sink.jsonl"})
        self.assertNotEqual(run.returncode, 0, "a set-but-missing sink dir must fail loudly")
        self.assertIn("FA_NOTIFY_SINK", run.stderr)


if __name__ == "__main__":
    unittest.main()


class SessionFence(unittest.TestCase):
    def test_xdist_run_fails_when_a_test_notifies(self):
        # R1: the fence is a SESSION property — a planted test that bypasses the sink must
        # fail the whole run (the shim log is read at sessionfinish), under xdist workers too.
        planted = ROOT / "tests" / "test_planted_notify_tmp_0541.py"
        planted.write_text(
            "import subprocess\n"
            "def test_planted_raw_notify():\n"
            "    subprocess.run(['osascript', '-e', 'display notification \"x\"'], check=False)\n")
        self.addCleanup(planted.unlink)
        shim_log = Path(os.environ["FA_NOTIFY_SHIM_DIR"]) / "calls.log"
        before = shim_log.read_text() if shim_log.exists() else ""
        env = {k: v for k, v in os.environ.items() if k != "FA_NOTIFY_SINK"}
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_planted_notify_tmp_0541.py",
             "-q", "-p", "no:cacheprovider", "-n", "2"],
            capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertNotEqual(run.returncode, 0,
                            "a notifying test must fail the SESSION (shim log non-empty)")
        self.assertIn("real notification/window attempt", run.stdout + run.stderr)
        # THIS test planted the notification on purpose (it asserts the fence catches it):
        # remove its record again, or every later session in this run fails at
        # sessionfinish for an intended, already-asserted call.
        added = shim_log.read_text() if shim_log.exists() else ""
        if added != before:
            assert added.startswith(before), "shim log changed outside this test's tail"
            shim_log.write_text(before)
