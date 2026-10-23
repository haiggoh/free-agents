#!/usr/bin/env python3
"""Leak B guard (0.41.1): the telemetry wrapper must not outlive its session.

The wrapper starts a background monitor (`tail -f` over the claude debug file) and then
`exec`s claude — which replaces the shell in place, so the EXIT trap and the MONITOR_PID
cleanup are dead code. Every remote free-API session that ever used this wrapper left its
monitor subshell and its `tail -f` behind, parented to init (measured 2026-10-20: 48;
still leaking 2026-10-23: orphan pairs up to 2 days old).

This test runs the wrapper with a stub claude that creates the debug file, lingers a
moment, and exits — then asserts the monitor and the tail are gone within seconds. The
stub session id is unique to this run, so the pgrep patterns can never match a real
session's monitor; the test only ever observes (and cleans up) its own leak.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "bin" / "la-remote-telemetry-wrapper.sh"


class TelemetryWrapperWatchdog(unittest.TestCase):
    def test_monitor_and_tail_die_after_claude_exits(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        sid = f"watchdog-{os.getpid()}-{time.time_ns()}"     # unique: never a real session

        stub = root / "stub-claude"
        stub.write_text(
            "#!/bin/bash\n"
            "# stub claude: the wrapper inserts --debug-file <path> before the rest;\n"
            "# create the debug file (the monitor waits for it), linger briefly, exit.\n"
            'if [[ "${1:-}" = "--debug-file" ]]; then : > "$2"; fi\n'
            "sleep 2\n"
        )
        stub.chmod(0o755)

        self._patterns = (f"la-remote-telemetry-wrapper.sh {sid}",
                          f"tail -f /tmp/claude-debug-{sid}.log")
        self.addCleanup(self._kill_own_leak)

        out = root / "wrapper.out"
        with open(out, "w") as fh:
            proc = subprocess.Popen(["bash", str(WRAPPER), sid, str(stub)],
                                    stdout=fh, stderr=fh, start_new_session=True)
        proc.wait(timeout=20)

        alive: list[str] = []
        deadline = time.time() + 12
        while time.time() < deadline:
            alive = self._leaked()
            if not alive:
                break
            time.sleep(1)
        self.assertEqual(alive, [], "wrapper monitor/tail survived the session (Leak B)")

    def _leaked(self) -> list[str]:
        pids: list[str] = []
        for pattern in self._patterns:
            r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
            pids += [p for p in r.stdout.split() if p]
        return pids

    def _kill_own_leak(self):
        """After the assertion, never leave this run's leak behind (its session id is unique
        to this test, so these kills can only ever touch this test's own processes)."""
        if not getattr(self, "_patterns", None):
            return
        for pid in self._leaked():
            subprocess.run(["kill", "-9", pid], capture_output=True)
        debug_file = Path(f"/tmp/claude-debug-{self._patterns[0].split()[-1].split('claude-debug-')[-1]}.log")
        debug_file.unlink(missing_ok=True)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
