#!/usr/bin/env python3
"""watch-until — one generic transient watcher: engine + predicates + --self-test.

Interface under test (plan 2026-11-08-free-agents-routing-fallback-watcher, Task 1):
    watch-until <predicate> [args] [--deadline 4h] [--min-interval 10] [--max-interval 120]
                [--on-detect "ACTION"] [--quiet|--verbose] [--self-test] [--help]
    predicates: --git-tag REPO TAG | --git-refs-change REPO | --file-regex PATH REGEX |
                --port-listen PORT | --pid-exit PID [--proc-start EPOCH] |
                --session-progress SID (--regex R | --stall MIN) [--proxy-log PATH] |
                --cmd 'SHELL TEST'
    exit: 0 DETECTED, 1 TIMEOUT, 2 usage, 3 CANNOT_START (first probe cannot run),
          4 STALL; stdout is exactly one verdict line in quiet mode.

Env: FA_WATCH_PROJECTS_DIR overrides the transcript root (~/.claude/projects) — tests plant
transcripts there; the watcher itself never writes outside $TMPDIR.
"""
import importlib.machinery
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "watch-until"


def load_module():
    """Import the extensionless bin/watch-until as a module (for the injected-clock test)."""
    loader = importlib.machinery.SourceFileLoader("watch_until", str(BIN))
    spec = importlib.util.spec_from_loader("watch_until", loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run_watch(args, env=None, timeout=40):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, str(BIN), *args], capture_output=True,
                          text=True, env=e, timeout=timeout)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def make_remote_with_clone(tmp):
    """A bare 'origin' with one seeded ref, plus a clone of it. Returns (remote, clone)."""
    tmp = Path(tmp)
    remote = tmp / "remote.git"
    _git("init", "--bare", str(remote), cwd=tmp)
    seed = tmp / "seed"
    _git("init", str(seed), cwd=tmp)
    (seed / "f.txt").write_text("x")
    _git("add", "f.txt", cwd=seed)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed", cwd=seed)
    _git("push", str(remote), "HEAD:refs/heads/main", cwd=seed)
    # a bare repo's HEAD defaults to master; point it at main so the clone below is not empty
    # (git tag fails in an empty clone: "Failed to resolve 'HEAD' as a valid ref")
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=remote)
    clone = tmp / "clone"
    _git("clone", str(remote), str(clone), cwd=tmp)
    return remote, clone


class GitTagPredicate(unittest.TestCase):
    def test_git_tag_lightweight_and_annotated_both_detect(self):
        with tempfile.TemporaryDirectory() as tmp:
            _remote, clone = make_remote_with_clone(tmp)
            for tag, annotated in (("tag-lw", False), ("tag-ann", True)):
                proc = subprocess.Popen(
                    [sys.executable, str(BIN), "--git-tag", str(clone), tag,
                     "--deadline", "10", "--min-interval", "0.2"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                time.sleep(0.8)                      # let the first probe run: tag absent, polling
                if annotated:
                    _git("tag", "-a", tag, "-m", "m", cwd=clone)
                else:
                    _git("tag", tag, cwd=clone)
                _git("push", "origin", tag, cwd=clone)
                out, _err = proc.communicate(timeout=15)
                self.assertEqual(proc.returncode, 0, f"{tag}: {out}")
                self.assertTrue(out.startswith("DETECTED"), out)
                self.assertIn(tag, out)

    def test_git_tag_absent_times_out_exit1(self):
        with tempfile.TemporaryDirectory() as tmp:
            _remote, clone = make_remote_with_clone(tmp)
            r = run_watch(["--git-tag", str(clone), "never-tag",
                           "--deadline", "2", "--min-interval", "0.2"])
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertTrue(r.stdout.startswith("TIMEOUT"), r.stdout)


class FirstProbeFailure(unittest.TestCase):
    def test_empty_first_probe_exit3(self):
        # a path that is not a git repo: ls-remote fails with empty stdout -> CANNOT_START, not "absent"
        r = run_watch(["--git-tag", "/nonexistent-repo-xyz", "v1", "--deadline", "2"])
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("CANNOT_START"), r.stdout)
        # a refs-change snapshot that cannot run (no origin) is also exit 3
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "no-origin.git"
            _git("init", str(bare), cwd=tmp)
            r2 = run_watch(["--git-refs-change", str(bare), "--deadline", "2"])
            self.assertEqual(r2.returncode, 3, r2.stdout + r2.stderr)
        # a missing transcript dir is exit 3, never "absent"
        with tempfile.TemporaryDirectory() as tmp:
            r3 = run_watch(["--session-progress", "sid-x", "--regex", "y", "--deadline", "2"],
                           env={"FA_WATCH_PROJECTS_DIR": tmp})
            self.assertEqual(r3.returncode, 3, r3.stdout + r3.stderr)
            self.assertTrue(r3.stdout.startswith("CANNOT_START"), r3.stdout)


class RefsChange(unittest.TestCase):
    def test_refs_change_detects_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote, clone = make_remote_with_clone(tmp)
            proc = subprocess.Popen(
                [sys.executable, str(BIN), "--git-refs-change", str(clone),
                 "--deadline", "10", "--min-interval", "0.2"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.8)
            seed = Path(tmp) / "seed"
            (seed / "g.txt").write_text("y")
            _git("add", "g.txt", cwd=seed)
            _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "c2", cwd=seed)
            _git("push", str(remote), "HEAD:refs/heads/extra", cwd=seed)
            out, _err = proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 0, out)
            self.assertTrue(out.startswith("DETECTED"), out)


class FileRegex(unittest.TestCase):
    def test_file_regex(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "state.txt"
            f.write_text("hello world")
            r = run_watch(["--file-regex", str(f), "hello", "--deadline", "2"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(r.stdout.startswith("DETECTED"), r.stdout)
            # control: absent pattern times out
            r2 = run_watch(["--file-regex", str(f), "zzz-nope", "--deadline", "1",
                            "--min-interval", "0.2"])
            self.assertEqual(r2.returncode, 1, r2.stdout)
            # a file that appears later is detected (missing at first probe is NOT exit 3 here)
            late = Path(tmp) / "late.txt"
            proc = subprocess.Popen(
                [sys.executable, str(BIN), "--file-regex", str(late), "needle",
                 "--deadline", "6", "--min-interval", "0.2"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.5)
            late.write_text("a needle appears")
            out, _ = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, out)
            self.assertTrue(out.startswith("DETECTED"), out)


class PortListen(unittest.TestCase):
    def test_port_listen(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))          # scratch port, dynamically allocated (>= 4300 in smoke)
        s.listen(1)
        port = s.getsockname()[1]
        try:
            r = run_watch(["--port-listen", str(port), "--deadline", "3", "--min-interval", "0.1"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(r.stdout.startswith("DETECTED"), r.stdout)
        finally:
            s.close()
        # control: a closed port times out
        s2 = socket.socket()
        s2.bind(("127.0.0.1", 0))
        closed = s2.getsockname()[1]
        s2.close()
        r2 = run_watch(["--port-listen", str(closed), "--deadline", "1", "--min-interval", "0.1"])
        self.assertEqual(r2.returncode, 1, r2.stdout)


class PidExit(unittest.TestCase):
    def test_pid_exit_and_reused_pid_by_epoch(self):
        mod = load_module()
        # process exits -> DETECTED
        p = subprocess.Popen(["sleep", "1"])
        try:
            r = run_watch(["--pid-exit", str(p.pid), "--deadline", "8", "--min-interval", "0.2"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(r.stdout.startswith("DETECTED"), r.stdout)
        finally:
            p.wait(timeout=10)
        # pid alive but start time differs from --proc-start -> pid reused -> DETECTED
        p2 = subprocess.Popen(["sleep", "30"])
        try:
            r2 = run_watch(["--pid-exit", str(p2.pid), "--proc-start", "1",
                            "--deadline", "3", "--min-interval", "0.1"])
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertIn("reused", r2.stdout)
            # control: the CORRECT start epoch with the process alive -> no detection -> TIMEOUT
            epoch = mod.proc_start_epoch(p2.pid)
            self.assertIsNotNone(epoch)
            r3 = run_watch(["--pid-exit", str(p2.pid), "--proc-start", str(epoch),
                            "--deadline", "1", "--min-interval", "0.1"])
            self.assertEqual(r3.returncode, 1, r3.stdout)
        finally:
            p2.kill()
            p2.wait(timeout=10)


class SessionProgress(unittest.TestCase):
    def _projects(self, tmp, sid, content):
        proj = Path(tmp) / f"proj-{sid}"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / f"{sid}.jsonl").write_text(content)
        return {"FA_WATCH_PROJECTS_DIR": tmp}

    def test_session_regex_detect(self):
        with tempfile.TemporaryDirectory() as tmp:
            sid = "sess-regex-1"
            env = self._projects(tmp, sid, '{"text":"the handoff marker is here"}\n')
            r = run_watch(["--session-progress", sid, "--regex", "handoff marker",
                           "--deadline", "3"], env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(r.stdout.startswith("DETECTED"), r.stdout)
            # control: pattern that never appears -> TIMEOUT
            r2 = run_watch(["--session-progress", sid, "--regex", "absent-xyz",
                            "--deadline", "1", "--min-interval", "0.2"], env=env)
            self.assertEqual(r2.returncode, 1, r2.stdout)

    def test_session_stall_exit4_with_proxy_diagnosis(self):
        with tempfile.TemporaryDirectory() as tmp:
            sid = "sess-stall-1"
            env = self._projects(tmp, sid, '{"text":"stalled session"}\n')
            t = Path(tmp) / f"proj-{sid}" / f"{sid}.jsonl"
            old = time.time() - 120
            os.utime(t, (old, old))          # transcript last grew 120 s ago
            log = Path(tmp) / "proxy-4399.log"
            log.write_text(
                "info: request ok\n"
                "la_proxy_hooks: NVIDIA 429 — upstream rate limited; "
                "model throttle: mistral-large-4-1t-a52b cools 30s (2. in a row)\n"
                "Anthropic Adapter - mid-stream error: 'NoneType' object has no attribute '__aiter__'\n")
            r = run_watch(["--session-progress", sid, "--stall", "0.02",
                           "--proxy-log", str(log), "--deadline", "5", "--min-interval", "0.1"],
                          env=env)
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertTrue(r.stdout.startswith("STALL"), r.stdout)
            self.assertIn("NVIDIA 429", r.stdout, "the STALL verdict names the proxy-log diagnosis")


class Backoff(unittest.TestCase):
    def test_backoff_doubles_and_caps(self):
        mod = load_module()
        PR = mod.ProbeResult

        class FakeClock:
            def __init__(self):
                self.t = 0.0
                self.sleeps = []

            def now(self):
                return self.t

            def sleep(self, s):
                self.sleeps.append(s)
                self.t += s

        clk = FakeClock()
        calls = {"n": 0}

        def constant_probe(_now):
            calls["n"] += 1
            return PR(detected=False, stall=False, value="same", evidence="", cannot_start=False)

        code, verdict = mod.run_engine(constant_probe, deadline_s=300, min_interval=10,
                                       max_interval=120, now=clk.now, sleep=clk.sleep)
        self.assertEqual(code, 1)
        self.assertTrue(verdict.startswith("TIMEOUT"), verdict)
        self.assertEqual(clk.sleeps, [10, 20, 40, 80, 120, 120],
                         "interval doubles while unchanged and caps at max-interval")

        # reset on change: the probe's value changes on the 3rd poll -> interval resets to min
        clk2 = FakeClock()
        values = iter(["a", "a", "b", "b", "b", "b", "b", "b", "b"])

        def changing_probe(_now):
            return PR(detected=False, stall=False, value=next(values, "b"), evidence="",
                      cannot_start=False)

        code2, _ = mod.run_engine(changing_probe, deadline_s=300, min_interval=10,
                                  max_interval=120, now=clk2.now, sleep=clk2.sleep)
        self.assertEqual(code2, 1)
        self.assertEqual(clk2.sleeps[:4], [10, 20, 10, 20],
                         "a changed probe result resets the backoff to min-interval")


class CliContract(unittest.TestCase):
    def test_help_never_polls(self):
        t0 = time.monotonic()
        r = run_watch(["--help"])
        self.assertLess(time.monotonic() - t0, 1.0, "--help returns in under a second")
        self.assertEqual(r.returncode, 0)
        self.assertIn("watch-until", r.stdout)
        self.assertIn("--git-tag", r.stdout)
        # --help wins even alongside a predicate that would fail if probed
        r2 = run_watch(["--help", "--git-tag", "/nonexistent-xyz", "v"])
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_unknown_flag_exit2(self):
        r = run_watch(["--bogus-flag"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("usage", r.stderr.lower())

    def test_quiet_prints_exactly_one_verdict_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "s.txt"
            f.write_text("ok")
            r = run_watch(["--file-regex", str(f), "ok", "--deadline", "2", "--quiet"])
            self.assertEqual(r.returncode, 0)
            lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1, f"exactly one verdict line, got: {r.stdout!r}")
            self.assertTrue(lines[0].startswith("DETECTED"), lines[0])
            r2 = run_watch(["--file-regex", str(f), "nope", "--deadline", "1",
                            "--min-interval", "0.2", "--quiet"])
            self.assertEqual(r2.returncode, 1)
            lines2 = [ln for ln in r2.stdout.splitlines() if ln.strip()]
            self.assertEqual(len(lines2), 1, f"exactly one verdict line, got: {r2.stdout!r}")
            self.assertTrue(lines2[0].startswith("TIMEOUT"), lines2[0])

    def test_self_test_exit0(self):
        r = run_watch(["--self-test"], timeout=90)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SELF-TEST", r.stdout)


if __name__ == "__main__":
    unittest.main()
