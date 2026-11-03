#!/usr/bin/env python3
"""tests/test_picker_startup_frame.py — what the user sees in the first ~0.3 s of the picker.

Regression (cbd6302): bin/session-picker cleared to a blank screen ~0.25 s after the shell had
already shown the launch command, so that noise stayed visible until the clear landed. The first
bytes must now be the alternate screen plus the ⏳ loading frame from config/emoji.sh; a pipe gets
no escape bytes at all; an early exit (venv missing) must give the screen and cursor back.
"""
import os
import pty
import re
import select
import subprocess
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PICKER = ROOT / "bin/session-picker"


def loading_emoji():
    return re.search(r'^EMOJI_LOADING="([^"]*)"', (ROOT / "config/emoji.sh").read_text(), re.M).group(1)


def pty_bytes(env_extra, until=b"", secs=30.0):
    """Everything the picker writes to a PTY until it EXITS (EOF/EIO), `until` appears, or the
    `secs` ceiling. Waiting on the exit, not on a fixed window: a fixed 3 s read killed a picker
    that had not started yet on a loaded machine and returned b'' (2026-11-03)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(str(PICKER), [str(PICKER)], {**os.environ, "TERM": "xterm-256color", **env_extra})
    buf, end = b"", time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:             # EIO: the child exited and closed the PTY
                break
            if not chunk:               # EOF
                break
            buf += chunk
        if until and until in buf:
            break
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    return buf


class StartupFrame(unittest.TestCase):
    def test_first_bytes_are_the_loading_frame_not_a_bare_clear(self):
        # A venv that does not exist makes the wrapper exit right after the frame, so no Textual
        # is needed and the test cannot hang on a UI.
        buf = pty_bytes({"LA_PICKER_VENV": "/nonexistent"})
        frame = ("\x1b[?1049h\x1b[2J\x1b[H\x1b[?25l  " + loading_emoji() + " loading.  ").encode()
        self.assertTrue(buf.startswith(frame), repr(buf[:80]))

    def test_early_exit_restores_screen_and_cursor(self):
        buf = pty_bytes({"LA_PICKER_VENV": "/nonexistent"})
        self.assertIn(b"\x1b[?1049l", buf, "the alternate screen must be left on an early exit")
        self.assertIn(b"\x1b[?25h", buf.split(b"\x1b[?1049h", 1)[1], "the cursor must come back")

    def test_a_slow_start_is_waited_for_not_cut_off(self):
        # Regression (2026-11-03, load average 28): pty_bytes read for a FIXED 3 s and killed the
        # picker before its first byte, so the early-exit test saw b''. BASH_ENV delays the bash
        # wrapper's start by 4 s, deterministically; the reader must wait for the child to exit.
        slow = Path(os.environ.get("TMPDIR", "/tmp")) / f"picker-slow-start-{os.getpid()}.sh"
        slow.write_text("sleep 4\n")
        try:
            buf = pty_bytes({"LA_PICKER_VENV": "/nonexistent", "BASH_ENV": str(slow)})
        finally:
            slow.unlink()
        self.assertIn(b"\x1b[?1049l", buf, "a picker that starts slowly must still be read to its exit")

    def test_pipe_and_help_get_no_escape_bytes(self):
        piped = subprocess.run([str(PICKER)], env={**os.environ, "LA_PICKER_VENV": "/nonexistent"},
                               capture_output=True, stdin=subprocess.DEVNULL)
        self.assertNotIn(b"\x1b", piped.stdout + piped.stderr)
        self.assertEqual(piped.returncode, 3)
        helped = subprocess.run([str(PICKER), "--help"], capture_output=True, stdin=subprocess.DEVNULL)
        self.assertNotIn(b"\x1b", helped.stdout)
        self.assertEqual(helped.returncode, 0)


if __name__ == "__main__":
    unittest.main()
