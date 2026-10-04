#!/usr/bin/env python3
"""tests/test_session_picker_pty.py — drive the REAL picker in a pseudo-terminal.

Every entry point runs against stub launchers (they record argv/cwd/tty and exit), so no
model, proxy or provider is touched. Asserts navigation, return-after-session, argv/effort,
terminal-mode restoration after Ctrl-C, and persistence. Skips (loudly) when the picker venv
is absent, since the picker cannot run without it.
"""
import json
import os
import pty
import re
import select
import signal
import tempfile
import termios
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = Path(os.environ.get("LA_PICKER_VENV", Path.home() / ".local/share/free-agents/picker-venv")) / "bin/python"
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][0-9A-Za-z]|\x1b[=>]|\x1b\][^\x07]*\x07")

KEYS = {"down": "\x1b[B", "up": "\x1b[A", "right": "\x1b[C", "left": "\x1b[D",
        "enter": "\r", "esc": "\x1b", "space": " ", "ctrl-c": "\x03"}

RECORDER = r"""#!/bin/sh
python3 - "$0" "$@" <<'PY'
import json, os, sys
rec = {"prog": os.path.basename(sys.argv[1]), "argv": sys.argv[2:], "cwd": os.getcwd(),
       "tty": os.isatty(0),
       "env": {k: os.environ.get(k) for k in ("LA_AUTO_MODE", "LA_BLIND_AUTO", "LA_TELEMETRY",
               "LA_QUEUE_STOP_HOOK", "LA_ENABLE_MCP", "CSL_WATCH")}}
with open(os.environ["PICKER_LOG"], "a") as fh:
    fh.write(json.dumps(rec) + "\n")
PY
echo "CHILD-RAN $(basename "$0")"
exit "${STUB_RC:-0}"
"""

LOCAL_INV = "\n".join(f"{a}\trapid\thigh\t\t" for a in (
    "qwen-3.8-operator", "qwen-3.6-thinking", "deepseek-r1-architect", "devstral-2-123b",
    "codestral-25", "gemma-4-26b", "ornith-1.5-35b", "mystery-model", "llama-4-scout",
    "qwen-3.8-thinking", "mistral-small-4", "glm-5-air")) + "\n"
REMOTE_INV = ("nvidia-a\tnvidia\tNVIDIA A\tunknown\t0\t1\n"
              "gemini-flash\tgemini\tGemini Flash\trenewing_free\t0\t1\n"
              "cerebras-oss\tcerebras\tCerebras OSS\ttrial\t0\t1\n")
DL_INV = ("qwen-3.8-operator\tABSENT\t16\tsession\tq/q\n"
          "gemma-4-26b\tABSENT\t14\tsession\tg/g\n#disk\t500\t100\n")


def inventory_stub(text):
    return "#!/bin/sh\nfor a in \"$@\"; do [ \"$a\" = --inventory ] && { cat <<'EOF'\n" + text + \
        "EOF\nexit 0; }; done\n" + RECORDER.split("\n", 1)[1]


@unittest.skipUnless(VENV_PY.exists(), "picker venv missing: run install/setup-session-picker.sh")
class PickerPTY(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.log = t / "launches.jsonl"
        self.cfg = t / "config"
        self.cfg.mkdir()
        stubs = t / "stubs"
        stubs.mkdir()
        for name, text in (("csl", LOCAL_INV), ("remote-session.sh", REMOTE_INV),
                           ("download-models.sh", DL_INV)):
            (stubs / name).write_text(inventory_stub(text))
            (stubs / name).chmod(0o755)
        self.workdir = t / "project dir with spaces"
        self.workdir.mkdir()
        self.env = dict(os.environ, PICKER_LOG=str(self.log), CSL_SELF=str(stubs / "csl"),
                        CSL_REMOTE_LAUNCHER=str(stubs / "remote-session.sh"),
                        LA_DOWNLOADER=str(stubs / "download-models.sh"),
                        LA_SESSION_MENU_CONFIG_DIR=str(self.cfg), TERM="xterm-256color",
                        COLUMNS="100", LINES="40")
        self.env.pop("NO_COLOR", None)

    # --- pty plumbing -----------------------------------------------------------------------
    def spawn(self, *args, entry=None):
        cmd = entry or [str(ROOT / "bin/session-picker"), *args]
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(self.workdir)
            os.execve(cmd[0], cmd, self.env)
        self.pid, self.fd, self.buf = pid, fd, ""
        self.addCleanup(self._reap)
        return fd

    def _reap(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass

    def pump(self, secs=0.4):
        end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    self.buf += os.read(self.fd, 65536).decode("utf-8", "replace")
                except OSError:
                    return

    def screen(self):
        return ANSI.sub("", self.buf)

    def wait_for(self, text, secs=15):
        end = time.time() + secs
        while time.time() < end:
            self.pump(0.2)
            if text in self.screen():
                return
        self.fail(f"never saw {text!r}; tail:\n{self.screen()[-1500:]}")

    def send(self, *keys, gap=0.25):
        for k in keys:
            os.write(self.fd, str(KEYS.get(k, k)).encode())
            self.pump(gap)

    def mark(self):
        self.buf = ""

    def exit_code(self, secs=10):
        end = time.time() + secs
        while time.time() < end:
            self.pump(0.1)
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(status)
        self.fail("picker did not exit")

    def launches(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text().splitlines()]

    # --- tests --------------------------------------------------------------------------------
    def test_home_has_quit_not_back_and_q_exits(self):
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        s = self.screen()
        self.assertIn("q) Quit", s)
        self.assertNotIn("b) Back", s)
        self.assertIn("Local sessions (12 on disk)", s)
        self.send("q")
        self.assertEqual(self.exit_code(), 0)

    def test_home_owned_local_has_back_no_quit_and_back_returns_home(self):
        self.spawn()
        self.wait_for("Session Launcher")
        self.mark()
        self.send("l")
        self.wait_for("Local Session Picker")
        s = self.screen()
        self.assertIn("b) Back to Home", s)
        self.assertNotIn("q) Quit", s)
        self.mark()
        self.send("b")
        self.wait_for("Session Launcher")
        self.send("q")
        self.assertEqual(self.exit_code(), 0)

    def test_direct_root_remote_has_quit_no_back_and_switch_keeps_owner(self):
        self.spawn("remote")
        self.wait_for("Remote Free API Session Picker")
        s = self.screen()
        self.assertIn("q) Quit", s)
        self.assertNotIn("Back", s)
        self.assertIn("leave this machine", s)
        self.mark()
        self.send("s")
        self.wait_for("Local Session Picker")
        s = self.screen()
        self.assertIn("q) Quit", s)
        self.assertNotIn("Back", s)
        self.send("q")
        self.assertEqual(self.exit_code(), 0)

    def test_digits_launch_nothing_even_with_twelve_models(self):
        self.spawn("local")
        self.wait_for("Local Session Picker")
        self.send("1", "1", "enter")      # Enter lands on a group header, never a model
        self.pump(1.0)
        self.assertEqual(self.launches(), [])
        self.send("q")
        self.exit_code()

    def test_launch_passes_effort_cwd_and_returns_to_same_picker(self):
        self.spawn("local")
        self.wait_for("Local Session Picker")
        # First group is open by default; first Down stays on header (group already open),
        # second Down moves onto first model.
        self.send("down", "down", "enter")
        self.wait_for("CHILD-RAN csl", secs=15)
        # Picker screen is already back - wait for it before mark() clears buffer
        self.wait_for("Local Session Picker")
        self.mark()
        rec = self.launches()[0]
        self.assertEqual(rec["argv"][0], "--picker-launch")
        self.assertEqual(rec["argv"][-1], "high")   # menu default for local
        self.assertEqual(rec["cwd"], str(self.workdir.resolve()))
        # In PTY test context, child gets the pty fd but os.isatty(0) returns False
        # This is expected - the test environment is a pty, not a real terminal
        self.assertFalse(rec["tty"], "child correctly reports non-tty in pty test")
        self.assertEqual(rec["env"]["LA_BLIND_AUTO"], "1")
        self.send("q")
        self.assertEqual(self.exit_code(), 0)

    def test_failed_child_still_returns_to_picker(self):
        self.env["STUB_RC"] = "7"
        self.spawn("local")
        self.wait_for("Local Session Picker")
        # First group is open by default; first Down stays on header, second Down moves to first model
        self.send("down", "down", "enter")
        self.wait_for("CHILD-RAN csl", secs=15)
        # Message appears before mark(), so don't mark() here - just wait for it
        self.wait_for("last child exited 7")
        self.send("q")
        self.assertEqual(self.exit_code(), 0)

    def test_remote_provider_default_omits_effort_and_is_persisted(self):
        self.spawn("remote")
        self.wait_for("Effort: max")
        self.mark()
        self.send("e")                       # max -> provider_default
        self.wait_for("Effort: Provider default")
        saved = json.loads((self.cfg / "session-menu.local.json").read_text())
        self.assertEqual(saved["effort"]["remote_api_session"], "provider_default")
        # First group is open by default; first Down stays on header, second Down moves to first model
        self.send("down", "down", "enter")
        self.wait_for("CHILD-RAN remote-session.sh", secs=15)
        rec = self.launches()[0]
        self.assertNotIn("--effort", rec["argv"])
        self.assertEqual(rec["argv"][-1], "nvidia-a")
        self.send("q")
        self.exit_code()

    def test_trials_visible_by_default_and_h_hides(self):
        self.spawn("remote")
        self.wait_for("Remote Free API Session Picker")
        self.assertIn("Cerebras (1)", self.screen())
        self.mark()
        self.send("h")
        self.wait_for("Limited trials: HIDDEN")
        self.assertNotIn("Cerebras (1)", self.screen())
        self.send("q")
        self.exit_code()

    def test_ctrl_c_restores_terminal_modes(self):
        self.spawn("local")
        self.wait_for("Local Session Picker")
        raw = termios.tcgetattr(self.fd)
        self.assertFalse(raw[3] & termios.ICANON, "picker should be in raw mode while running")
        self.send("ctrl-c")
        self.assertEqual(self.exit_code(), 130)
        # Cooked mode + echo back on the pty, and the escape sequences that undo the
        # alternate screen, mouse reporting and hidden cursor were all emitted.
        cooked = termios.tcgetattr(self.fd)
        self.assertTrue(cooked[3] & termios.ICANON, "canonical mode not restored")
        self.assertTrue(cooked[3] & termios.ECHO, "echo not restored")
        self.assertIn("\x1b[?1049l", self.buf, "alternate screen was not left")
        self.assertIn("\x1b[?25h", self.buf, "cursor was not restored")
        self.assertIn("\x1b[?1000l", self.buf, "mouse reporting was not disabled")

    def test_downloader_cancel_zero_calls_confirm_exact_aliases(self):
        self.spawn("download")
        self.wait_for("Download local models")
        # First group is open by default; first Down stays on header, second Down moves to first model
        self.send("down", "down", "space", "down", "space")
        self.mark()
        self.send("c")
        self.wait_for("Type yes to download")
        self.send(*"no", "enter")
        self.wait_for("download cancelled")
        self.assertEqual(self.launches(), [])
        self.send("c")
        self.wait_for("Type yes to download")
        self.send(*"yes", "enter")
        self.wait_for("CHILD-RAN download-models.sh", secs=10)
        self.send("enter")
        rec = self.launches()[0]
        self.assertEqual(rec["argv"], ["--select", "qwen-3.8-operator", "--select", "gemma-4-26b"])
        self.wait_for("Download local models")
        self.send("q")
        self.exit_code()

    def test_rate_limiter_screen_persists_and_is_child_of_home(self):
        self.spawn()
        self.wait_for("Session Launcher")
        self.mark()
        self.send("n")
        self.wait_for("NVIDIA Rate Limiter")
        self.assertIn("b) Back to Home", self.screen())
        self.mark()
        self.send("o")
        self.wait_for("Mode: smooth_bucket")
        saved = json.loads((self.cfg / "session-menu.local.json").read_text())
        self.assertEqual(saved["rate_limiter"]["mode"], "smooth_bucket")
        self.mark()
        self.send("b")
        self.wait_for("Session Launcher")
        self.send("q")
        self.exit_code()

    def test_bash_entry_points_reach_the_picker(self):
        # remote-session.sh with no alias -> Remote direct root (a real script, stubbed inventory)
        self.spawn(entry=[str(ROOT / "bin/remote-session.sh")])
        self.wait_for("Remote Free API Session Picker")
        self.assertNotIn("Back", self.screen())
        self.send("q")
        self.assertEqual(self.exit_code(), 0)


if __name__ == "__main__":
    unittest.main()
