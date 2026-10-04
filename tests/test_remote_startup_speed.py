#!/usr/bin/env python3
"""tests/test_remote_startup_speed.py — remote-session.sh must not spawn a process per roster row.

Regression: _lc_load_policy ran four `python3 -c` per policy row and local-capable-filter.sh one
per roster row, so EVERY invocation (even --help) took ~5.5 s while Terminal.app still showed the
"Last login … % remote-session.sh ; exit;" lines. Counting python spawns under `bash -x` is a
stable proxy for that cost (wall-clock thresholds are flaky on a loaded machine).
"""
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class RemoteStartupSpeed(unittest.TestCase):
    def test_help_spawns_a_bounded_number_of_python_processes(self):
        # A python3 shim first on PATH logs every start, including those inside child scripts
        # whose stderr remote-session.sh discards (so tracing alone would miss them).
        import shutil, tempfile
        real = shutil.which("python3")
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "spawns"
            shim = Path(tmp) / "python3"
            shim.write_text(f'#!/bin/sh\necho x >> "{log}"\nexec "{real}" "$@"\n')
            shim.chmod(0o755)
            env = dict(os.environ, PATH=f"{tmp}:{os.environ['PATH']}")
            r = subprocess.run(["bash", str(ROOT / "bin/remote-session.sh"), "--help"], env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-500:])
            spawns = len(log.read_text().splitlines()) if log.exists() else 0
        rows = int(subprocess.run(["bash", "-c", 'source "$1"; echo ${#LA_REMOTE_AGENTS[@]}', "_",
                                   str(ROOT / "config/remote-agents.sh")], capture_output=True, text=True).stdout)
        self.assertGreater(rows, 10, "fixture sanity: the roster must be large enough to show the cost")
        self.assertGreater(spawns, 0, "the shim must see at least the policy parse (planted positive)")
        self.assertLess(spawns, 6, f"{spawns} python spawns for {rows} roster rows — a per-row loop is back")

if __name__ == "__main__":
    unittest.main()
