"""tests/picker_harness.py — shared fixtures for driving the session picker in tests.

Two drivers, one per layer:
  * PilotCase  — Textual's own test pilot (App.run_test). Reads widget state directly, so a
                 label or layout change breaks only the test that is about that label. Use it
                 for navigation, settings, persistence and the inline prompts.
  * PTYCase    — the real picker binary in a pseudo-terminal. Use it ONLY for what lives at
                 the terminal edge: entry points, Ctrl-C restoring the tty, exit codes.

Both run against stub launchers that record argv/cwd/env and exit, so no model, proxy,
provider or browser is ever touched (LA_URL_OPENER points at a recorder too).
"""
import asyncio
import contextlib
import json
import os
import pty
import re
import select
import signal
import sys
import tempfile
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


class _StubbedEnv(unittest.TestCase):
    """Temp dir with recorder stubs, a private config dir, a 700 keys dir, a workdir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name).resolve()          # resolved: the key Store refuses symlinks
        self.log = t / "launches.jsonl"
        self.cfg = t / "config"
        self.cfg.mkdir()
        self.keys = t / "api_keys"
        self.keys.mkdir(mode=0o700)
        stubs = t / "stubs"
        stubs.mkdir()
        for name, text in (("csl", LOCAL_INV), ("remote-session.sh", REMOTE_INV),
                           ("download-models.sh", DL_INV)):
            (stubs / name).write_text(inventory_stub(text))
            (stubs / name).chmod(0o755)
        (stubs / "url-opener").write_text(RECORDER)
        (stubs / "url-opener").chmod(0o755)
        self.workdir = t / "project dir with spaces"
        self.workdir.mkdir()
        self.env = {"PICKER_LOG": str(self.log), "CSL_SELF": str(stubs / "csl"),
                    "CSL_REMOTE_LAUNCHER": str(stubs / "remote-session.sh"),
                    "LA_DOWNLOADER": str(stubs / "download-models.sh"),
                    "LA_URL_OPENER": str(stubs / "url-opener"),
                    "LA_API_KEYS_DIR": str(self.keys),
                    "LA_SESSION_MENU_CONFIG_DIR": str(self.cfg)}

    def launches(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text().splitlines()]

    def saved_state(self):
        return json.loads((self.cfg / "session-menu.local.json").read_text())


@unittest.skipUnless(VENV_PY.exists(), "picker venv missing: run install/setup-session-picker.sh")
class PilotCase(_StubbedEnv):
    """Drive the picker in-process with Textual's pilot. Needs textual importable, i.e. run
    under the picker venv's python; skips otherwise."""

    def setUp(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("textual not importable: run under the picker venv python")
        super().setUp()
        self._saved_env = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.addCleanup(self._restore_env)
        sys.path.insert(0, str(ROOT / "bin"))
        import session_picker
        self.sp = session_picker
        import session_picker_model
        self.m = session_picker_model

    def _restore_env(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_picker(self, scenario, start="home", **flags):
        """Run `await scenario(app, pilot, h)` inside a picker started at `start`. Children run
        with the real suspend() disabled (no terminal to hand over) but are still executed."""
        import argparse
        args = argparse.Namespace(screen=start, include_trials=False, exclude_trials=False,
                                  local_capable_shown=False)
        for k, v in flags.items():
            setattr(args, k, v)
        cwd = os.getcwd()
        os.chdir(self.workdir)
        self.addCleanup(os.chdir, cwd)
        # run_child(pause=True) waits on input("Press Enter to return…"); answer it at once.
        import builtins
        real_input = builtins.input
        builtins.input = lambda *a, **k: ""
        self.addCleanup(setattr, builtins, "input", real_input)

        async def main():
            app = self.sp.Picker(start, args)
            app.suspend = contextlib.nullcontext
            async with app.run_test(size=(120, 50)) as pilot:
                h = PilotHelper(self, app, pilot)
                await h.settle()
                await scenario(app, pilot, h)
            return app
        return asyncio.run(main())


class PilotHelper:
    """Small vocabulary over the pilot: wait for state, read labels, move to a row by id."""

    def __init__(self, case, app, pilot):
        self.case, self.app, self.pilot = case, app, pilot

    async def settle(self, secs=1.5):
        """Let background inventory loads land (they post back via call_from_thread)."""
        end = time.time() + secs
        while time.time() < end:
            await self.pilot.pause(0.1)
            if not getattr(self.app, "_loading", set()):
                break
        await self.pilot.pause(0.2)

    async def press(self, *keys, gap=0.15):
        for k in keys:
            await self.pilot.press(k)
            await self.pilot.pause(gap)

    def title(self):
        from textual.widgets import Static
        return str(self.app.query_one("#title", Static).render())

    def status(self):
        from textual.widgets import Static
        return str(self.app.query_one("#status", Static).render())

    def labels(self, which="actions"):
        from textual.widgets import OptionList
        ol = self.app.query_one(f"#{which}", OptionList)
        return [str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]

    def text(self):
        return "\n".join([self.title(), *self.labels("actions"), *self.labels("rows"), self.status()])

    def screen(self):
        return type(self.app.screen_model).__name__

    async def wait_for(self, pred, what, secs=8):
        end = time.time() + secs
        while time.time() < end:
            if pred():
                return
            await self.pilot.pause(0.1)
        self.case.fail(f"timed out waiting for {what}; screen={self.screen()}\n{self.text()}")

    async def wait_text(self, needle, secs=8):
        await self.wait_for(lambda: needle in self.text(), repr(needle), secs)

    async def wait_screen(self, name, secs=8):
        await self.wait_for(lambda: self.screen() == name, f"screen {name}", secs)

    async def goto_row(self, kind, row_id, max_steps=60):
        """Move down until the highlighted row is (kind, row_id), opening its group if needed.
        Navigation by IDENTITY, never by counting key presses."""
        for _ in range(max_steps):
            h = self.app._highlighted()
            if h == (kind, row_id):
                return
            await self.press("down", gap=0.05)
        self.case.fail(f"never reached {kind}:{row_id}; last highlight {self.app._highlighted()}")

    async def open_group(self, group_id):
        acc = self.app.screen_model.accordion
        if acc.open_group != group_id:
            await self.goto_row("group", group_id)
            await self.press("enter")


@unittest.skipUnless(VENV_PY.exists(), "picker venv missing: run install/setup-session-picker.sh")
class PTYCase(_StubbedEnv):
    """The real binary in a pseudo-terminal, for terminal-edge behaviour only."""

    def setUp(self):
        super().setUp()
        self.env = dict(os.environ, **self.env, TERM="xterm-256color", COLUMNS="100", LINES="40")
        self.env.pop("NO_COLOR", None)

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

    def exit_code(self, secs=10):
        end = time.time() + secs
        while time.time() < end:
            self.pump(0.1)
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(status)
        self.fail("picker did not exit")
