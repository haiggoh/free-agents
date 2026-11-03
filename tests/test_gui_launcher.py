#!/usr/bin/env python3
"""Thin launcher contract (plan Task 3): install/gui-launcher.py + install/gui-launcher/locate.sh.

locate.sh is the platform-neutral launcher contract (the macOS applet and, later, the Linux
Desktop Entry both call it). It runs from Finder with launchd's minimal environment, so every
locate test runs under `env -i` with a temp HOME — never the user's plugin cache or config.
Generation tests write into a temp --dest and a temp HOME; nothing touches ~/Applications.

Usage: python3 tests/test_gui_launcher.py [-v]
"""
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "install/gui-launcher.py"
LOCATE = ROOT / "install/gui-launcher/locate.sh"
APP = "Free Agents.app"
BUNDLE_ID = "io.github.haiggoh.free-agents.launcher"
DARWIN = sys.platform == "darwin"
FINDER_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def finder_env(home: Path, **extra) -> dict:
    env = {"HOME": str(home), "PATH": FINDER_PATH, "TMPDIR": tempfile.gettempdir()}
    env.update(extra)
    return env


def fake_root(base: Path, name: str, gui: bool = True, gui_body: str = "") -> Path:
    """A minimal plugin dir: manifest + (optionally) an executable bin/free-agents-gui."""
    root = base / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name": "free-agents", "version": "%s"}' % name)
    if gui:
        (root / "bin").mkdir()
        f = root / "bin/free-agents-gui"
        f.write_text("#!/bin/sh\n" + (gui_body or 'touch "$LA_GUI_READY_FILE"; exit 0') + "\n")
        f.chmod(0o755)
    return root


def fake_venv(base: Path) -> Path:
    """A 'venv' whose python answers the import probe (exit 0), without NiceGUI."""
    v = base / "gui-venv"
    (v / "bin").mkdir(parents=True)
    py = v / "bin/python"
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    return v


def write_conf(home: Path, **kv) -> None:
    d = home / ".config/free-agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / "gui-launcher.conf").write_text("".join(f"{k}='{v}'\n" for k, v in kv.items()))


def locate(home: Path, *args, timeout=60, **extra):
    return subprocess.run(["/bin/sh", str(LOCATE), *args], capture_output=True, text=True,
                          env=finder_env(home, **extra), cwd="/", timeout=timeout)


class LocateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.cache = self.home / ".claude/plugins/cache/haiggoh/free-agents"
        self.cache.mkdir(parents=True)
        self.venv = fake_venv(self.home)

    def tearDown(self):
        self._tmp.cleanup()

    def test_help(self):
        r = locate(self.home, "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Usage", r.stdout)

    def test_unknown_flag(self):
        r = locate(self.home, "--bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Usage", r.stderr)

    def test_highest_cache_version_with_gui_wins_under_finder_env(self):
        fake_root(self.cache, "0.9.0")
        fake_root(self.cache, "0.10.0")         # numeric, not lexical: 0.10.0 > 0.9.0
        write_conf(self.home, GUI_VENV=self.venv)
        r = locate(self.home, "--print-root")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(self.cache / "0.10.0"))

    def test_cache_version_without_gui_is_skipped_for_hint(self):
        fake_root(self.cache, "0.50.4", gui=False)          # an installed version predating the GUI
        hint = fake_root(self.home / "src", "checkout")
        write_conf(self.home, GUI_VENV=self.venv, PLUGIN_HINT=hint)
        r = locate(self.home, "--print-root")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(hint))

    def test_explicit_override_beats_cache(self):
        fake_root(self.cache, "0.99.0")
        pin = fake_root(self.home / "src", "pinned")
        write_conf(self.home, GUI_VENV=self.venv, FREE_AGENTS_PLUGIN=pin)
        r = locate(self.home, "--print-root")
        self.assertEqual(r.stdout.strip(), str(pin), r.stderr)

    def test_invalid_override_aborts_naming_it(self):
        fake_root(self.cache, "0.99.0")
        write_conf(self.home, GUI_VENV=self.venv, FREE_AGENTS_PLUGIN=self.home / "nope")
        r = locate(self.home, "--print-root")
        self.assertEqual(r.returncode, 2)
        self.assertIn("FREE_AGENTS_PLUGIN", r.stderr)

    def test_nothing_found_is_actionable(self):
        write_conf(self.home, GUI_VENV=self.venv)
        r = locate(self.home, "--print-root")
        self.assertEqual(r.returncode, 4)
        self.assertIn("Free Agents is not installed", r.stderr)

    def test_new_plugin_version_found_without_regeneration(self):
        fake_root(self.cache, "0.51.0")
        write_conf(self.home, GUI_VENV=self.venv)
        self.assertTrue(locate(self.home, "--print-root").stdout.strip().endswith("0.51.0"))
        fake_root(self.cache, "0.52.0")         # a plugin upgrade lands; the .app is untouched
        self.assertTrue(locate(self.home, "--print-root").stdout.strip().endswith("0.52.0"))

    def test_missing_venv_prints_setup_command(self):
        fake_root(self.cache, "0.51.0")
        write_conf(self.home, GUI_VENV=self.home / "no-venv")
        r = locate(self.home, "--check")
        self.assertEqual(r.returncode, 3)
        self.assertIn("setup-gui.sh", r.stderr)

    def test_launch_returns_after_ready(self):
        fake_root(self.cache, "0.51.0")
        write_conf(self.home, GUI_VENV=self.venv)
        t0 = time.time()
        r = locate(self.home, "--launch")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.time() - t0, 20)

    def test_launch_reports_crash_with_log_tail(self):
        fake_root(self.cache, "0.51.0", gui_body='echo "boom: nicegui import failed" >&2; exit 1')
        write_conf(self.home, GUI_VENV=self.venv)
        r = locate(self.home, "--launch")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("boom: nicegui import failed", r.stderr)

    def test_launch_detaches_all_stdio(self):
        # do shell script waits for stdout to close: a child keeping it open hangs the applet.
        fake_root(self.cache, "0.51.0", gui_body='touch "$LA_GUI_READY_FILE"; sleep 30')
        write_conf(self.home, GUI_VENV=self.venv)
        t0 = time.time()
        r = subprocess.run(["/bin/sh", str(LOCATE), "--launch"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=finder_env(self.home), timeout=25)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.time() - t0, 15, "locate.sh held the pipe open until the child exited")
        subprocess.run(["pkill", "-f", str(self.cache / "0.51.0/bin/free-agents-gui")])


@unittest.skipUnless(DARWIN, "macOS applet generation")
class GenerateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.dest = self.home / "Applications"
        self.dest.mkdir()
        self.app = self.dest / APP
        self.venv = fake_venv(self.home)   # check runs locate.sh --check, which needs a venv

    def tearDown(self):
        self._tmp.cleanup()

    def gen(self, *args):
        env = dict(os.environ, HOME=str(self.home), LA_GUI_VENV=str(self.venv))
        env.pop("XDG_CONFIG_HOME", None)
        return subprocess.run([sys.executable, str(GEN), *args, "--dest", str(self.dest)],
                              capture_output=True, text=True, env=env, timeout=120)

    def plist(self):
        return plistlib.loads((self.app / "Contents/Info.plist").read_bytes())

    def test_help(self):
        r = subprocess.run([sys.executable, str(GEN), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        for word in ("generate", "check", "repair", "remove", "--dry-run"):
            self.assertIn(word, r.stdout)

    def test_generate_into_tmp_creates_owned_valid_bundle(self):
        r = self.gen("generate")
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.plist()
        self.assertEqual(p["CFBundleIdentifier"], BUNDLE_ID)
        self.assertEqual(p["CFBundleName"], "Free Agents")
        self.assertEqual(p["FreeAgentsLauncherContract"], 1)
        self.assertTrue((self.app / "Contents/Resources/locate.sh").is_file())
        v = subprocess.run(["codesign", "--verify", "--strict", "--deep", str(self.app)],
                           capture_output=True, text=True)
        self.assertEqual(v.returncode, 0, v.stderr)
        conf = (self.home / ".config/free-agents/gui-launcher.conf").read_text()
        self.assertIn(f"PLUGIN_HINT='{ROOT}'", conf)

    def test_bundle_holds_no_python_or_plugin_copy(self):
        self.assertEqual(self.gen("generate").returncode, 0)
        files = [p.name for p in self.app.rglob("*") if p.is_file()]
        self.assertFalse([f for f in files if f.endswith((".py", ".pyc", ".so", ".dylib"))], files)

    def test_generate_twice_is_idempotent(self):
        self.assertEqual(self.gen("generate").returncode, 0)
        exe = self.app / "Contents/MacOS/applet"
        before = exe.stat().st_ino
        r = self.gen("generate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("up to date", r.stdout)
        self.assertEqual(exe.stat().st_ino, before)

    def test_repair_regenerates(self):
        self.assertEqual(self.gen("generate").returncode, 0)
        before = (self.app / "Contents/MacOS/applet").stat().st_ino
        r = self.gen("repair")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotEqual((self.app / "Contents/MacOS/applet").stat().st_ino, before)

    def test_refuses_foreign_app_at_dest(self):
        (self.app / "Contents").mkdir(parents=True)
        info = self.app / "Contents/Info.plist"
        info.write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.other"}))
        before = info.read_bytes()
        r = self.gen("repair")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not ours", r.stderr)
        self.assertEqual(info.read_bytes(), before)

    def test_dry_run_writes_nothing(self):
        r = self.gen("generate", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.app.exists())
        self.assertFalse((self.home / ".config").exists())

    def test_check_reports_ok_then_missing(self):
        self.assertEqual(self.gen("generate").returncode, 0)
        self.assertEqual(self.gen("check").returncode, 0)
        shutil.rmtree(self.app)
        self.assertNotEqual(self.gen("check").returncode, 0)

    def test_remove_only_owned_and_keeps_config(self):
        self.assertEqual(self.gen("generate").returncode, 0)
        conf = self.home / ".config/free-agents/gui-launcher.conf"
        r = self.gen("remove")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.app.exists())
        self.assertTrue(conf.exists(), "removing the launcher must keep the user's config")


if __name__ == "__main__":
    unittest.main()
