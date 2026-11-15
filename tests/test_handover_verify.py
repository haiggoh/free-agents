#!/usr/bin/env python3
"""handover-verify.sh — liveness by EPOCH SECONDS (plan 2026-11-08 route-watcher Task 4).

Bug (0.55.5, measured 2026-11-08): the registry's procStart is written in UTC while
`ps lstart` prints LOCAL time, so the old string comparison reported "pid recycled" for the
same instant (registry 18:44:10 vs ps 19:44:10 on a CET box). The registry also carries
startedAt as epoch MILLIS — the authoritative field. Fix: compare epoch seconds with a ±2 s
tolerance (startedAt/1000 vs the live pid's lstart parsed in local time); fall back to
procStart parsed as UTC when startedAt is absent.

Runs the script with TZ=Europe/Berlin (and TZ=UTC) so the UTC-vs-local split is exercised.
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "handover-verify.sh"


def live_start_epoch(pid):
    """The live pid's ps lstart parsed as LOCAL time -> epoch seconds (same as the impl)."""
    r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                       capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"})
    line = " ".join(r.stdout.split())
    return time.mktime(time.strptime(line, "%a %b %d %H:%M:%S %Y"))


class HandoverVerifyEpoch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.sdir = t / "sessions"
        self.sdir.mkdir()
        self.pdir = t / "projects" / "proj-x"
        self.pdir.mkdir(parents=True)
        self.proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(self._kill)

    def _kill(self):
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:
            pass

    def _registry(self, start_epoch_s, sid="sess-hv-1"):
        """A registry entry whose procStart is the same instant written in UTC (as the real
        harness writes it) and startedAt as epoch millis."""
        doc = {"pid": self.proc.pid, "sessionId": sid,
               "startedAt": int(start_epoch_s * 1000),
               "procStart": time.strftime("%a %b %e %H:%M:%S %Y", time.gmtime(start_epoch_s))}
        # ps prints space-padded days ("Nov  8"); the impl collapses whitespace, the registry
        # string may use either — write the space-padded form like the real file
        doc["procStart"] = time.strftime("%a %b ", time.gmtime(start_epoch_s)) + \
            f"{time.gmtime(start_epoch_s).tm_mday:2d} " + \
            time.strftime("%H:%M:%S %Y", time.gmtime(start_epoch_s))
        (self.sdir / f"{self.proc.pid}.json").write_text(json.dumps(doc))

    def _run(self, tz, timeout=5):
        env = {**os.environ, "TZ": tz, "LC_ALL": "C"}
        return subprocess.run(
            ["bash", str(SCRIPT), "--expect", "handoff.md", "--pid", str(self.proc.pid),
             "--timeout", str(timeout), "--sessions-dir", str(self.sdir),
             "--projects-dir", str(self.pdir.parent)],
            capture_output=True, text=True, env=env, timeout=timeout + 20)

    def test_same_instant_utc_registry_vs_local_ps_is_not_recycled(self):
        for tz in ("Europe/Berlin", "UTC"):
            with self.subTest(tz=tz):
                live = live_start_epoch(self.proc.pid)
                self._registry(live)                      # startedAt = the live pid's start
                (self.pdir / "sess-hv-1.jsonl").write_text('{"text":"see handoff.md"}\n')
                r = self._run(tz)
                self.assertEqual(r.returncode, 0,
                                 f"TZ={tz}: same instant in UTC registry vs local ps must "
                                 f"VERIFY, got: {r.stdout}{r.stderr}")
                self.assertIn("VERIFIED", r.stdout)

    def test_timeout_hint_names_the_trust_prompt(self):
        # 0.56.8: a handover launched into a fresh worktree sat at the folder-trust dialog while
        # handover-verify reported a bare timeout — the hint must name the actual cause.
        r = subprocess.run(
            ["bash", str(SCRIPT), "--expect", "never-written-needle", "--session-id", "sess-nope",
             "--timeout", "1", "--sessions-dir", str(self.sdir),
             "--projects-dir", str(self.pdir.parent)],
            capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"}, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("NOT_VERIFIED", r.stdout)
        self.assertIn("trust", r.stdout.lower())
        # 0.56.12: the old hint told the user to "relaunch with --trust-workspace", a flag Claude
        # Code 2.1.322 rejects; the hint must name the second real cause instead.
        self.assertIn("exited at startup", r.stdout)
        self.assertNotIn("relaunch with --trust-workspace", r.stdout)

    def test_genuinely_different_start_reports_recycled(self):
        live = live_start_epoch(self.proc.pid)
        self._registry(live + 5)                          # 5 s off > 2 s tolerance
        (self.pdir / "sess-hv-1.jsonl").write_text('{"text":"see handoff.md"}\n')
        for tz in ("Europe/Berlin", "UTC"):
            with self.subTest(tz=tz):
                r = self._run(tz)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("recycled", r.stdout)

    def test_startedat_registration_lag_is_not_recycled(self):
        # 2026-11-10 false positive: with no procStart, startedAt (REGISTRATION time, after
        # the folder-trust dialog) lagged the process start by ~1 min and was compared at ±2 s.
        # It now gets the wide 300 s fallback window.
        live = live_start_epoch(self.proc.pid)
        doc = {"pid": self.proc.pid, "sessionId": "sess-hv-1",
               "startedAt": int((live + 60) * 1000)}      # registration lagged 60 s, no procStart
        (self.sdir / f"{self.proc.pid}.json").write_text(json.dumps(doc))
        (self.pdir / "sess-hv-1.jsonl").write_text('{"text":"see handoff.md"}\n')
        for tz in ("Europe/Berlin", "UTC"):
            with self.subTest(tz=tz):
                r = self._run(tz)
                self.assertEqual(r.returncode, 0,
                                 f"TZ={tz}: a 60 s registration lag must VERIFY, got: {r.stdout}{r.stderr}")
                self.assertIn("VERIFIED", r.stdout)

    def test_startedat_far_off_still_reports_recycled(self):
        live = live_start_epoch(self.proc.pid)
        doc = {"pid": self.proc.pid, "sessionId": "sess-hv-1",
               "startedAt": int((live + 400) * 1000)}     # beyond the 300 s registration window
        (self.sdir / f"{self.proc.pid}.json").write_text(json.dumps(doc))
        (self.pdir / "sess-hv-1.jsonl").write_text('{"text":"see handoff.md"}\n')
        r = self._run("Europe/Berlin")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("recycled", r.stdout)


class HandoverVerifyFallbackNotify(unittest.TestCase):
    """0.59.0 Task 4: when pre-trust could not hold and the verify times out with no
    registered session, handover-verify sends ONE notification via fa_notify (the sink
    FA_NOTIFY_SINK fences it in tests)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.sdir = t / "sessions"
        self.sdir.mkdir()
        self.pdir = t / "projects" / "proj-x"
        self.pdir.mkdir(parents=True)
        self.sink = t / "notify-sink.jsonl"
        self.target = t / "handover-target"
        self.target.mkdir()

    def _run(self, extra_env=None, cwd=None, timeout=1):
        env = {**os.environ, "LC_ALL": "C", "FA_NOTIFY_SINK": str(self.sink)}
        env.pop("FA_PRE_TRUST_TARGET", None)
        env.pop("FA_PRE_TRUST_RESULT", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(SCRIPT), "--expect", "never-written-needle", "--session-id", "sess-nope",
             "--timeout", str(timeout), "--sessions-dir", str(self.sdir),
             "--projects-dir", str(self.pdir.parent)],
            capture_output=True, text=True, env=env, timeout=timeout + 20, cwd=cwd)

    def _sink_lines(self):
        if not self.sink.exists():
            return []
        return [json.loads(line) for line in self.sink.read_text().splitlines() if line.strip()]

    def test_waiting_dialog_notifies_once(self):
        r = self._run({"FA_PRE_TRUST_TARGET": str(self.target), "FA_PRE_TRUST_RESULT": "4"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("NOT_VERIFIED", r.stdout)
        lines = self._sink_lines()
        self.assertEqual(len(lines), 1, f"exactly one notification per run, got {lines}")
        self.assertEqual(lines[0]["title"], "free-agents handover waiting")

    def test_notify_text_names_the_folder(self):
        r = self._run({"FA_PRE_TRUST_TARGET": str(self.target)})
        self.assertEqual(r.returncode, 1)
        lines = self._sink_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn(str(self.target), lines[0]["text"])
        self.assertIn("trust", lines[0]["text"].lower())

    def test_notify_falls_back_to_launch_cwd(self):
        # no FA_PRE_TRUST_TARGET: the launch cwd the verifier runs in is the target
        r = self._run(cwd=str(self.target))
        self.assertEqual(r.returncode, 1)
        lines = self._sink_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn(str(self.target), lines[0]["text"])

    def test_registered_session_never_notifies(self):
        # session registers and verifies -> no timeout -> no notification
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        live = live_start_epoch(proc.pid)
        doc = {"pid": proc.pid, "sessionId": "sess-ok",
               "startedAt": int(live * 1000),
               "procStart": time.strftime("%a %b ", time.gmtime(live)) +
                   f"{time.gmtime(live).tm_mday:2d} " + time.strftime("%H:%M:%S %Y", time.gmtime(live))}
        (self.sdir / f"{proc.pid}.json").write_text(json.dumps(doc))
        (self.pdir / "sess-ok.jsonl").write_text('{"text":"see never-written-needle"}\n')
        env = {**os.environ, "LC_ALL": "C", "FA_NOTIFY_SINK": str(self.sink),
               "FA_PRE_TRUST_TARGET": str(self.target)}
        r = subprocess.run(
            ["bash", str(SCRIPT), "--expect", "never-written-needle", "--pid", str(proc.pid),
             "--timeout", "5", "--sessions-dir", str(self.sdir),
             "--projects-dir", str(self.pdir.parent)],
            capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERIFIED", r.stdout)
        self.assertEqual(self._sink_lines(), [], "a verified session must never notify")

    def test_timeout_with_registered_session_never_notifies(self):
        # transcript EXISTS (session registered, working) but lacks the needle ->
        # timeout -> NO trust notification (the dialog case is: NO transcript at all)
        (self.pdir / "sess-nope.jsonl").write_text('{"text":"working on something else"}\n')
        r = self._run({"FA_PRE_TRUST_TARGET": str(self.target)})
        self.assertEqual(r.returncode, 1)
        self.assertIn("NOT_VERIFIED", r.stdout)
        self.assertEqual(self._sink_lines(), [],
                         "a registered session that times out must not trigger the trust notification")

    def test_notify_target_resolves_from_launcher_pid_cwd(self):
        # The launcher runs in the NEW window; its exported FA_PRE_TRUST_TARGET never
        # reaches this process. With --pid the target resolves from the launched
        # process's cwd via lsof (same trick as local-watch.sh:141), then $PWD.
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        live = live_start_epoch(proc.pid)
        doc = {"pid": proc.pid, "sessionId": "sess-pid",
               "startedAt": int(live * 1000),
               "procStart": time.strftime("%a %b ", time.gmtime(live)) +
                   f"{time.gmtime(live).tm_mday:2d} " + time.strftime("%H:%M:%S %Y", time.gmtime(live))}
        (self.sdir / f"{proc.pid}.json").write_text(json.dumps(doc))
        stubdir = Path(self.tmp.name) / "stubs"
        stubdir.mkdir()
        fake_cwd = self.target  # the launched window's folder
        (stubdir / "lsof").write_text(
            '#!/bin/sh\n# $1=-p $2=pid; lsof column $4=="cwd", $NF=path\n'
            f'echo "claude $2 user cwd DIR 1 {fake_cwd}"\n')
        (stubdir / "lsof").chmod(0o755)
        env = {**os.environ, "LC_ALL": "C", "FA_NOTIFY_SINK": str(self.sink),
               "PATH": str(stubdir) + ":" + os.environ["PATH"]}
        env.pop("FA_PRE_TRUST_TARGET", None)
        env.pop("FA_PRE_TRUST_RESULT", None)
        r = subprocess.run(
            ["bash", str(SCRIPT), "--expect", "never-written-needle", "--pid", str(proc.pid),
             "--timeout", "1", "--sessions-dir", str(self.sdir),
             "--projects-dir", str(self.pdir.parent)],
            capture_output=True, text=True, env=env, timeout=30, cwd=str(self.sdir))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        lines = self._sink_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn(str(fake_cwd), lines[0]["text"],
                      "the notification must name the LAUNCH cwd (lsof), not the verifier's $PWD")


if __name__ == "__main__":
    unittest.main()
