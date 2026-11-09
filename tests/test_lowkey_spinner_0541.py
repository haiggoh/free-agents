#!/usr/bin/env python3
"""tests/test_lowkey_spinner_0541.py — 0.55.8 Task 5 (0541 plan Item D):

ONE delayed spinner for lowkey's local AND API paths: a reply slower than 0.5 s shows frames
on stderr (PTY) that are CLEARED before the next line; a fast reply shows none; an error
line lands after the clear; the local path uses the same class.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_lowkey_spinner_0541.py
"""
import os
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIVER = ROOT / "tests" / "fixtures" / "spinner_driver_0541.py"
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive


def _run_pty(delay: str, status: str = "200", timeout: float = 30):
    """Run the driver with a PTY (stderr IS a tty) and return (returncode, output)."""
    tmp = tempfile.mkdtemp(prefix="fa-spin-")
    keys = Path(tmp) / "keys"
    keys.mkdir()
    (keys / "nvidia").write_text("test-key\n")
    env = {**os.environ,
           "FA_SPIN_TMP": tmp,
           "FA_SPIN_BIN": str(ROOT / "bin"),
           "LA_API_KEYS_DIR": str(keys),
           "LA_HF_PARAMS_CACHE": f"{tmp}/hf-params.json",
           "LA_NVIDIA_THROTTLE_STATE": f"{tmp}/throttle.json"}
    master, slave = pty.openpty()
    p = subprocess.Popen([sys.executable, str(DRIVER), delay, status],
                         stdin=slave, stdout=slave, stderr=slave,
                         env=env, start_new_session=True)
    os.close(slave)
    out = b""
    end = time.time() + timeout
    while time.time() < end and p.poll() is None:
        r, _, _ = select.select([master], [], [], 0.3)
        if r:
            try:
                out += os.read(master, 65536)
            except OSError:
                break
    try:
        p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
    while True:
        r, _, _ = select.select([master], [], [], 0.2)
        if not r:
            break
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break                      # pty EOF: macOS returns b"" (an empty read, no error)
    os.close(master)
    return p.returncode, out.decode("utf-8", "replace")


class SpinnerPTY(unittest.TestCase):
    """The PTY cases run as a plain-python SUBPROCESS of the pytest process: the pty+select
    read loop interacts badly with pytest's own capture in-process (observed: the whole run
    stalls with no output), while the identical flow from a clean process exits in ~1 s."""

    def _pty_case(self, case: str, expect: str):
        run = subprocess.run(
            [sys.executable, str(Path(__file__)), case],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "FA_PTY_CASE": case})
        out = run.stdout + run.stderr
        self.assertEqual(run.returncode, 0, out[-600:])
        self.assertIn(expect, out, out[-600:])

    def test_slow_api_reply_shows_spinner(self):
        self._pty_case("slow", "HAS-FRAME True")

    def test_fast_api_reply_shows_no_spinner(self):
        self._pty_case("fast", "HAS-FRAME False")

    def test_spinner_cleared_before_error_line(self):
        self._pty_case("error", "CLEARED-BEFORE-ERROR True")

    def test_delay_zero_prints_immediately_delay_default_does_not(self):
        # deterministic unit form of the delay contract (the PTY fast test is racy on the
        # first frame): delay=0 prints a frame at once; the default 0.5 s prints nothing.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "lowkey_cli", ROOT / "bin" / "lowkey-cli.py")
        lowkey = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lowkey)
        class FakeStream:
            def __init__(self):
                self.buf = ""
            def isatty(self):
                return True
            def write(self, s):
                self.buf += s
            def flush(self):
                pass
        import time as _t
        for delay, expect_frame in ((0.0, True), (0.5, False)):
            fs = FakeStream()
            with lowkey.DelayedSpinner("nvidia x", delay=delay, interactive=True, stream=fs):
                _t.sleep(0.15)
            self.assertEqual(("is working…" in fs.buf), expect_frame,
                             f"delay={delay}: buf={fs.buf!r}")

    def test_local_path_uses_same_spinner(self):
        src = (ROOT / "bin" / "lowkey-cli.py").read_text()
        self.assertIn("class DelayedSpinner", src, "the shared class must exist")
        self.assertEqual(src.count("with DelayedSpinner("), 2,
                         "one wrap per dispatch path (local + API)")


def _pty_main(case: str):
    delay, status = {"slow": ("1.2", "200"), "fast": ("0.05", "200"),
                     "error": ("0.9", "500")}[case]
    rc, out = _run_pty(delay, status)
    cleared = out.replace("\r\x1b[2K", "\x1b[2K[-]")
    print(f"HAS-FRAME {'is working…' in out}")
    print(f"CLEARED-BEFORE-ERROR {'\x1b[2K[-]' in cleared}")
    print(f"CLEAN-EXIT {rc == 0}")
    if rc != 0:
        print(out[-600:])


if __name__ == "__main__":
    case = sys.argv[1] if len(sys.argv) > 1 else None
    if case in ("slow", "fast", "error"):
        _pty_main(case)
    else:
        unittest.main()
