#!/usr/bin/env python3
"""tests/test_new_features.py — Comprehensive tests for new session picker features.

Tests for:
- RuntimeManagerScreen (Phase 5)
- APIKeysScreen (Phase 6)
- Left/Right arrow navigation for action items
- Emoji constants from config/emoji.sh
- Last-launched model persistence
- Loading indicator (200ms)
- Emoji spacing fixes
"""

import json
import os
import pty
import re
import select
import signal
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


@unittest.skipUnless(VENV_PY.exists(), "picker venv missing: run install/setup-session-picker.sh")
class NewFeaturesPTY(unittest.TestCase):
    """PTY tests for new features in the session picker."""

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

    # --- tests for RuntimeManagerScreen (Phase 5) -------------------------------------------
    def test_runtime_manager_screen_accessible_from_home(self):
        """Runtime Manager screen is accessible from Home via 'v' key."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("v")
        self.wait_for("Rapid-MLX Runtime Manager")
        screen = self.screen()
        self.assertIn("Rapid-MLX Runtime Manager", screen)
        # Should have actions for releases, install, promote, smoke, snapshot, remove
        self.assertIn("List installable releases", self.screen())
        self.assertIn("Install a release", self.screen())
        self.assertIn("Promote pins", self.screen())
        self.assertIn("Smoke-test", self.screen())
        self.assertIn("Snapshot", self.screen())
        self.assertIn("Remove", self.screen())
        self.send("q")
        self.assertEqual(self.exit_code(), 0)

    def test_runtime_manager_shows_installed_versions(self):
        """Runtime Manager shows installed versions with status."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("v")
        self.wait_for("Rapid-MLX Runtime Manager")
        screen = self.screen()
        # Should show installed versions or "no versioned environments"
        self.assertTrue("installed" in screen.lower() or "no versioned" in screen.lower())
        self.send("q")
        self.assertEqual(self.exit_code(), 0)

    def test_runtime_manager_releases_list(self):
        """Runtime Manager can list releases from PyPI."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("v")
        self.wait_for("Rapid-MLX Runtime Manager")
        self.mark()
        self.send("r")
        self.wait_for("Available Rapid-MLX releases")
        _ = self.screen()
        # Should list versions
        self.assertRegex(self.screen(), r"\d+\.\d+\.\d+")
        self.send("q")
        self.exit_code()

    def test_runtime_manager_install_dry_run(self):
        """Runtime Manager install dry-run works."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("v")
        self.wait_for("Rapid-MLX Runtime Manager")
        self.mark()
        self.send("i")
        self.wait_for("Choose version number")
        # Cancel with blank input
        self.send("enter")
        self.wait_for("Rapid-MLX Runtime Manager")
        self.send("q")
        self.exit_code()

    # --- tests for APIKeysScreen (Phase 6) --------------------------------------------------
    def test_api_keys_screen_accessible_from_home(self):
        """API Keys screen is accessible from Home via 'k' key."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("k")
        self.wait_for("API Keys Setup")
        screen = self.screen()
        self.assertIn("API Keys Setup", screen)
        # Should list providers with status
        self.assertIn("Google Gemini", self.screen())
        self.assertIn("Groq", self.screen())
        self.assertIn("OpenRouter", self.screen())
        # Should have status icons
        self.assertIn("✅", self.screen()) or self.assertIn("❌", self.screen())
        self.send("q")
        self.assertEqual(self.exit_code(), 0)

    def test_api_keys_shows_provider_status(self):
        """API Keys screen shows provider status with icons."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("k")
        self.wait_for("API Keys Setup")
        _ = self.screen()
        # Should show status for each provider
        self.assertIn("Google Gemini", self.screen())
        self.assertIn("Groq", self.screen())
        # Status should have icons
        self.assertRegex(self.screen(), r"[✅❌⚠️]")
        self.send("q")
        self.assertEqual(self.exit_code(), 0)

    def test_api_keys_open_signup_page(self):
        """API Keys screen can open provider signup page via provider group."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("k")
        self.wait_for("API Keys Setup")
        self.mark()
        # NVIDIA is first group (expanded by default), select "Open NVIDIA signup page"
        self.send("enter")  # Select the first item (Open NVIDIA signup page)
        # Should attempt to open browser (will fail in headless but shouldn't crash)
        self.wait_for("API Keys Setup")
        self.send("q")
        self.exit_code()

    # --- tests for Left/Right arrow navigation (Phase 1/3) ---------------------------------
    def test_right_arrow_cycles_action_choice(self):
        """Right arrow on action item cycles choice forward."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("l")  # Go to Local
        self.wait_for("Local Session Picker")
        self.mark()
        # Navigate to Effort action
        self.send("down", "down")  # Move to first action (Effort)
        self.pump(0.5)
        # Right arrow should cycle effort
        self.send("right")
        self.wait_for("Effort: medium")  # high -> medium
        self.send("q")
        self.exit_code()

    def test_left_arrow_cycles_action_choice_backward(self):
        """Left arrow on action item cycles choice backward."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("l")
        self.wait_for("Local Session Picker")
        self.mark()
        # Navigate to Effort action
        self.send("down", "down")
        self.pump(0.5)
        # Left arrow should cycle backward
        self.send("left")
        self.wait_for("Effort: max")  # high <- max (wraps)
        self.send("q")
        self.exit_code()

    def test_right_left_arrow_on_boolean_toggles(self):
        """Right/Left arrows toggle boolean actions."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("l")
        self.wait_for("Local Session Picker")
        self.mark()
        # Navigate to MCPs (boolean toggle)
        self.send("down", "down", "down", "down", "down")  # Move to MCPs
        self.pump(0.5)
        # Right arrow should toggle
        self.send("right")
        self.wait_for("MCPs: ENABLED")
        # Left arrow should toggle back
        self.send("left")
        self.wait_for("MCPs: DISABLED")
        self.send("q")
        self.exit_code()

    # --- tests for Emoji constants (Phase 4) -----------------------------------------------
    def test_emoji_constants_from_config(self):
        """Emoji constants are loaded from config/emoji.sh."""
        import sys
        sys.path.insert(0, str(ROOT / "bin"))
        import emoji_constants as ec

        # Check key emojis are loaded
        self.assertEqual(ec.SESSION_EMOJI_LOCAL_STR, "🦾")
        self.assertEqual(ec.SESSION_EMOJI_FREE_API_STR, "📡")
        self.assertEqual(ec.EMOJI_EFFORT_STR, "⚙️ ")
        self.assertEqual(ec.EMOJI_NVIDIA_RATE_LIMITER_STR, "🚦")
        self.assertEqual(ec.EMOJI_TOOLS_STR, "🔧")
        self.assertEqual(ec.EMOJI_MCP_STR, "🔌")
        self.assertEqual(ec.EMOJI_KEY_STR, "🔑")
        self.assertEqual(ec.EMOJI_TOOLS_STR, "🔧")

    def test_emoji_constants_used_in_screens(self):
        """Emoji constants are used in screen titles and actions."""
        import sys
        sys.path.insert(0, str(ROOT / "bin"))
        import session_picker_model as m

        s = m.Settings()
        home = m.HomeScreen(m.Settings(), local_count=12, remote_count=36)
        acts = home.actions()

        # Check emojis are used
        local_action = next(a for a in acts if a.key == "l")
        self.assertIn("🦾", local_action.label)

        remote_action = next(a for a in acts if a.key == "r")
        self.assertIn("📡", remote_action.label)

    # --- tests for Last-launched persistence (Phase 3) ------------------------------------
    def test_last_launched_model_persisted_locally(self):
        """Last launched local model is persisted and shown in 'Go last' action."""
        self.spawn("local")
        self.wait_for("Local Session Picker")
        self.mark()
        self.send("down", "down", "enter")  # Launch first model
        self.wait_for("CHILD-RAN csl", secs=15)
        self.wait_for("Local Session Picker")
        self.mark()

        # Check "Go last" action appears
        _ = self.screen()
        self.assertIn("Go last:", self.screen())

        self.send("q")
        self.exit_code()

    def test_last_launched_remote_persisted(self):
        """Last launched remote model is persisted and shown in 'Go last' action."""
        self.spawn("remote")
        self.wait_for("Remote Free API Session Picker")
        self.mark()
        # Need installed agents to test - skip if none
        screen = self.screen()
        if "nvidia-a" in screen:
            self.send("down", "down", "enter")
            self.wait_for("CHILD-RAN remote-session.sh", secs=15)
            self.wait_for("Remote Free API Session Picker")
            self.mark()
            _ = self.screen()
            self.assertIn("Go last:", self.screen())
        self.send("q")
        self.exit_code()

    # --- tests for Loading indicator (Phase 2) ---------------------------------------------
    def test_loading_indicator_shows_after_200ms(self):
        """Loading indicator shows after 200ms delay."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("l")
        # Should show loading indicator after 200ms
        self.wait_for("⏳ loading…", secs=1)
        self.wait_for("Local Session Picker", secs=5)
        self.send("q")
        self.exit_code()

    def test_loading_indicator_no_flash_on_fast_ops(self):
        """Loading indicator doesn't flash on fast operations."""
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.mark()
        self.send("q")  # Fast exit
        self.assertEqual(self.exit_code(), 0)
        # Should not have shown loading indicator
        self.assertNotIn("⏳", self.screen())

    # --- tests for Emoji spacing fixes -----------------------------------------------------
    def test_emoji_spacing_in_effort_labels(self):
        """Effort labels have space after emoji."""
        import sys
        sys.path.insert(0, str(ROOT / "bin"))
        import session_picker_model as m

        s = m.Settings()
        local = m.LocalScreen(m.Settings(), models=[])
        acts = local.actions()
        effort_action = next(a for a in acts if a.key == "e")
        # Should have space after emoji
        self.assertIn("⚙️  Effort", effort_action.label)

        # Check remote screen
        remote = m.RemoteScreen(m.Settings(), agents=[])
        acts = remote.actions()
        effort_action = next(a for a in acts if a.key == "e")
        self.assertIn("⚙️  Effort", effort_action.label)

    def test_emoji_spacing_in_other_labels(self):
        """Other labels have consistent emoji spacing."""
        import sys
        sys.path.insert(0, str(ROOT / "bin"))
        import session_picker_model as m

        s = m.Settings()
        local = m.LocalScreen(m.Settings(), models=[])
        acts = local.actions()

        # Check various actions have proper spacing
        for action in acts:
            label = action.label
            # Should not have emoji stuck to text
            for emoji in ["🦾", "📡", "🔧", "💬", "🔑", "🔌", "🚀", "📥", "📌", "🔬", "📄", "🧹", "📦", "📡", "🦾", "🔖", "🏷️", "🔌"]:
                if emoji in label:
                    idx = label.index(emoji)
                    if idx + len(emoji) < len(label):
                        next_char = label[idx + len(emoji)]
                        # Should have space after emoji
                        self.assertEqual(next_char, " ",
                            f"Emoji {emoji} in '{label}' should be followed by space")


if __name__ == "__main__":
    unittest.main()