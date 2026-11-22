#!/usr/bin/env python3
"""Mixed-writer contract (design section 4.5): the pre-change module (pinned base commit
6a7cfd7) and the new module share one state file. Old code ignores the new keys but DROPS
them on its wholesale rewrites; new code re-writes them on every poll (self-heal within one
poll interval), and the window accounting stays exact (never overspends) under both writers.
Also runs the limiter CLI for real against a temp state (observability surface)."""
import importlib.util
import json
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"
BASE = "6a7cfd7"          # branch point: the old module
FROZEN = 1_000_000.0

spec = importlib.util.spec_from_file_location("rate_limiter", BIN / "rate_limiter.py")
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)


def _load_old(tmpdir):
    """Extract the pre-change module from git into tmpdir and import it (children call this
    too — the mp tests must not depend on fork inheritance)."""
    src = subprocess.run(["git", "show", f"{BASE}:bin/rate_limiter.py"], cwd=str(REPO),
                         capture_output=True, text=True, check=True).stdout
    # 0b (2026-11-22 baseline flake): two workers sharing ONE filename raced — a concurrent
    # write_text truncate landed between another worker's read and exec_module, which then
    # compiled an EMPTY module ("no attribute 'SlidingWindowLimiter'"). A unique name per
    # call removes the shared-write window entirely; the module NAME stays the same.
    p = Path(tmpdir) / f"old_rate_limiter_{uuid.uuid4().hex}.py"
    p.write_text(src)
    spec = importlib.util.spec_from_file_location("old_rate_limiter", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _old_worker(path, tmpdir, attempts, out):
    old = _load_old(tmpdir)
    b = old.SlidingWindowLimiter(path, rpm=4, clock=lambda: FROZEN)
    got = sum(1 for _ in range(attempts) if b.try_acquire() == 0.0)
    out.put(("old", got, ""))


def _new_worker(path, attempts, out):
    b = rl.SlidingWindowLimiter(path, rpm=4, clock=lambda: FROZEN)
    tok = rl._waiter_token("new")
    got = 0
    err = ""
    try:
        for _ in range(attempts):
            if b.try_acquire(token=tok) == 0.0:
                got += 1
    except Exception as exc:                    # any crash must fail the contract test
        err = f"{type(exc).__name__}: {exc}"
    out.put(("new", got, err))


class MixedWriterSchemaTests(unittest.TestCase):
    """Single-process schema compat: old code's rewrite is the wipe; new code self-heals."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("LA_NVIDIA_") and not k.startswith("LA_LIMITER_")}
        cfg = tempfile.TemporaryDirectory()
        self.addCleanup(cfg.cleanup)
        env["LA_SESSION_MENU_CONFIG_DIR"] = cfg.name
        patcher = unittest.mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _state(self):
        with open(self.path) as f:
            return json.load(f)

    def test_old_code_drops_new_keys_and_new_code_self_heals(self):
        old = _load_old(self.tmp.name)
        nb = rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: FROZEN)
        for _ in range(4):
            self.assertEqual(nb.try_acquire(), 0.0)         # window full (new code wrote it)
        t = rl._waiter_token("sess-A")
        self.assertGreater(nb.try_acquire(token=t), 0.0)     # registers a waiter
        self.assertIn(t, self._state()["waiters"])
        # OLD code's try_acquire rewrites the file wholesale: the waiters key is GONE — the
        # planted negative proving this test can see the wipe it must survive
        self.assertGreater(old.SlidingWindowLimiter(
            self.path, rpm=4, clock=lambda: FROZEN).try_acquire(), 0.0)
        st = self._state()
        self.assertNotIn("waiters", st, "old code drops the new key on its rewrite")
        self.assertEqual(len(st["stamps"]), 4, "old window accounting stays exact")
        # NEW code re-writes the registry on its very next poll: self-heal within one poll
        self.assertGreater(nb.try_acquire(token=t), 0.0)
        self.assertIn(t, self._state()["waiters"], "registry self-heals within one poll")

    def test_old_code_parses_the_new_format(self):
        old = _load_old(self.tmp.name)
        nb = rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: FROZEN)
        for _ in range(4):
            nb.try_acquire()
        t = rl._waiter_token("sess-A")
        nb.try_acquire(token=t)                              # file now carries waiters/deficit
        ob = old.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: FROZEN)
        self.assertEqual(ob.status()["in_window"], 4, "old code reads the new-format file")
        self.assertGreater(ob.try_acquire(), 0.0, "old code keeps working (window full)")

    def test_mixed_tokenless_callers_never_overspend(self):
        old = _load_old(self.tmp.name)
        nb = rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: FROZEN)
        ok = 0
        for i in range(6):
            limiter = old.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: FROZEN) \
                if i % 2 else nb
            ok += 1 if limiter.try_acquire() == 0.0 else 0
        self.assertEqual(ok, 4, "exactly `limit` admissions under alternating writers")
        self.assertEqual(self._state()["limit"], 4)
        json.loads(open(self.path).read())


class MixedWriterProcessTests(unittest.TestCase):
    """Cross-process: old and new workers hammer one file; old invariant (never overspend)
    holds, no worker crashes, and the new registry survives."""

    def test_old_and_new_processes_share_one_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state")
            rl.SlidingWindowLimiter(path, rpm=4, clock=lambda: FROZEN).status()   # create
            q = mp.Queue()
            procs = [mp.Process(target=_old_worker, args=(path, d, 15, q)) for _ in range(2)]
            procs += [mp.Process(target=_new_worker, args=(path, 5, q)) for _ in range(2)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(300)   # the box can be heavily loaded; 60s expired once under -n auto
            # 0b: assert AFTER every worker has joined — an early abort on the first failure
            # left siblings alive, and their writes raced the tmpdir cleanup
            # ("OSError 66 Directory not empty"), masking the real failure.
            for p in procs:
                self.assertFalse(p.is_alive(), "worker did not finish within 300s")
                self.assertEqual(p.exitcode, 0, "worker exited non-zero")
            results = []   # drain only AFTER every worker has exited: no lost puts
            while not q.empty():
                results.append(q.get())
            self.assertEqual(len(results), 4, "every worker reported (no crash)")
            for kind, got, err in results:
                self.assertEqual(err, "", f"{kind} worker crashed: {err}")
            old_admitted = sum(g for k, g, _ in results if k == "old")
            self.assertLessEqual(old_admitted, 4, "old code alone never overspends")
            with open(path) as f:
                st = json.load(f)
            self.assertLessEqual(len([s for s in st["stamps"]
                                      if s if isinstance(s, (dict, float))]), 4)
            # self-heal: one new-code poll re-writes the registry after any old wipe
            nb = rl.SlidingWindowLimiter(path, rpm=4, clock=lambda: FROZEN)
            nb.try_acquire(token=rl._waiter_token("final"))
            with open(path) as f:
                st = json.load(f)
            self.assertIn("waiters", st, "new code re-writes the registry (self-heal)")
            self.assertIn("deficit", st)


class ObservabilityCliTests(unittest.TestCase):
    """The CLI surface, run FOR REAL against a temp state (never the live file)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("LA_NVIDIA_") and not k.startswith("LA_LIMITER_")}
        env["LA_NVIDIA_THROTTLE_STATE"] = os.path.join(self.tmp.name, "s")
        env["LA_SESSION_MENU_CONFIG_DIR"] = self.tmp.name
        self.env = env

    def run_cli(self, *args, **kw):
        return subprocess.run([sys.executable, str(BIN / "rate_limiter.py"), *args],
                              capture_output=True, text=True, env=self.env, **kw)

    def test_help_status_owners_probe_set_owner(self):
        r = self.run_cli("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("lane-toggle", r.stdout, "new subcommand documented in --help")
        r = self.run_cli("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout)
        self.assertIn("waiters", st, "status shows the queue snapshot")
        r = self.run_cli("owners")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("waiting", json.loads(r.stdout), "owners reports live queue lengths")
        r = self.run_cli("probe")
        self.assertEqual(r.returncode, 0, r.stderr)
        pr = json.loads(r.stdout)
        self.assertIn("queue_position", pr)
        self.assertIsNone(pr["queue_position"], "not queued -> None")
        r = self.run_cli("set-owner", "sess-X", "--kind", "dispatch")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["owners"]["sess-X"]["kind"], "dispatch")
        r = self.run_cli("set-owner", "sess-X", "--priority")          # non-TTY: refused
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("user approval", r.stderr)
        r = self.run_cli("set-owner", "sess-X", "--priority", "--user-approved")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["owners"]["sess-X"]["priority"], True)

    def test_status_shows_a_registered_waiter(self):
        path = self.env["LA_NVIDIA_THROTTLE_STATE"]
        b = rl.SlidingWindowLimiter(path, rpm=4, clock=lambda: FROZEN)
        with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": "sess-Q"}):
            for _ in range(4):
                b.try_acquire()
            b.try_acquire(token=rl._waiter_token("sess-Q"))
        r = self.run_cli("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout)
        self.assertEqual(len(st["waiters"]), 1)
        self.assertEqual(st["waiters"][0]["owner"], "sess-Q")
        self.assertEqual(st["waiters"][0]["class"], "normal")


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    unittest.main(verbosity=2)
