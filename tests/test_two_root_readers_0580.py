#!/usr/bin/env python3
"""Two-root venv readers (0.58.0 plan Task 1).

0.50.0 moved venv WRITES to $XDG_DATA_HOME/free-agents/backends (default
~/.local/share/free-agents/backends) and the manager's Python already scans both
roots (install/managers/base.py discovery_roots, new root first). The SHELL
readers still scanned only ~/.venvs: la_discover_rapid_bin never saw a new-root
install and la_backend_repair_hint called a new-root binary "not ours". These
cases pin both readers across BOTH roots, plus the la_backend_roots helper that
Tasks 1-2 share. Only managed names count: <prefix>-<X.Y.Z> for rapid-mlx /
vllm-mlx / litellm — the user's own ~/.venvs/* entries are never listed.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]

XDG_REL = Path(".local") / "share" / "free-agents" / "backends"


def make_fake_venv(root: Path, dirname: str, binname: str) -> Path:
    """Layout la_backend_bin_health accepts: an executable bin plus a bin/python
    that imports encodings (symlink to the running interpreter). The health
    check's own behaviour is pinned by tests/test_backend_bin_health.sh."""
    vdir = root / dirname
    (vdir / "bin").mkdir(parents=True)
    (vdir / "bin" / "python").symlink_to(sys.executable)
    script = vdir / "bin" / binname
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return vdir


class TwoRootReadersTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.home.mkdir()
        self.new_root = self.home / XDG_REL
        self.legacy = self.home / ".venvs"
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_DATA_HOME", "LA_USER_STATE_DIR", "LA_RAPID_BIN",
                            "LA_VENV", "_LA_RAPID_SYSTEM_CANDIDATES",
                            "LA_LIVE_CONFIG_DIR", "LA_CONFIG_DIR", "_LA_LIVE_FOR")}
        env["HOME"] = str(self.home)
        env["PATH"] = "/usr/bin:/bin"  # no rapid-mlx on PATH
        env["_LA_RAPID_SYSTEM_CANDIDATES"] = ""  # skip the fixed brew candidates
        self.env = env

    def run_lib(self, snippet: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c",
             'source config/config-lib.sh >/dev/null 2>&1; ' + snippet],
            capture_output=True, text=True, cwd=REPO, env=self.env)

    def test_backend_roots_prints_new_root_first(self):
        out = self.run_lib("la_backend_roots").stdout.splitlines()
        self.assertEqual(out, [str(self.new_root), str(self.legacy)])

    def test_discovery_finds_a_new_root_venv(self):
        make_fake_venv(self.new_root, "rapid-mlx-1.0.3", "rapid-mlx")
        r = self.run_lib("la_discover_rapid_bin")
        self.assertEqual(r.stdout.strip(),
                         str(self.new_root / "rapid-mlx-1.0.3" / "bin" / "rapid-mlx"))
        self.assertEqual(r.returncode, 0)

    def test_discovery_still_finds_a_legacy_root_venv(self):
        make_fake_venv(self.legacy, "rapid-mlx-1.0.0", "rapid-mlx")
        r = self.run_lib("la_discover_rapid_bin")
        self.assertEqual(r.stdout.strip(),
                         str(self.legacy / "rapid-mlx-1.0.0" / "bin" / "rapid-mlx"))

    def test_discovery_prefers_newest_across_roots(self):
        make_fake_venv(self.legacy, "rapid-mlx-1.0.0", "rapid-mlx")
        make_fake_venv(self.new_root, "rapid-mlx-1.0.3", "rapid-mlx")
        self.assertEqual(self.run_lib("la_discover_rapid_bin").stdout.strip(),
                         str(self.new_root / "rapid-mlx-1.0.3" / "bin" / "rapid-mlx"))

    def test_discovery_same_version_both_roots_prefers_new_root(self):
        make_fake_venv(self.legacy, "rapid-mlx-1.0.3", "rapid-mlx")
        make_fake_venv(self.new_root, "rapid-mlx-1.0.3", "rapid-mlx")
        self.assertEqual(self.run_lib("la_discover_rapid_bin").stdout.strip(),
                         str(self.new_root / "rapid-mlx-1.0.3" / "bin" / "rapid-mlx"))

    def test_discovery_ignores_unmanaged_dirs(self):
        make_fake_venv(self.legacy, "rapid-mlx-foo", "rapid-mlx")
        make_fake_venv(self.new_root, "rapid-mlx-foo", "rapid-mlx")
        r = self.run_lib("la_discover_rapid_bin")
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(r.returncode, 1)

    def test_discovery_ignores_repair_aside_dirs(self):
        """A killed repair leaves `<name>.repair-<pid>-<ts>` behind; even a HEALTHY
        aside is never a candidate — an aside is not a version."""
        make_fake_venv(self.legacy, "rapid-mlx-0.16.10.repair-66562-1794664402", "rapid-mlx")
        make_fake_venv(self.new_root, "rapid-mlx-0.16.10", "rapid-mlx")
        self.assertEqual(self.run_lib("la_discover_rapid_bin").stdout.strip(),
                         str(self.new_root / "rapid-mlx-0.16.10" / "bin" / "rapid-mlx"))

    def test_repair_hint_names_repair_for_a_new_root_binary(self):
        r = self.run_lib('la_backend_repair_hint '
                         '"$HOME/.local/share/free-agents/backends/vllm-mlx-0.6.0/bin/vllm-mlx"')
        self.assertIn("--backend vllm-mlx repair 0.6.0", r.stdout)

    def test_repair_hint_still_names_repair_for_a_legacy_binary(self):
        r = self.run_lib('la_backend_repair_hint "$HOME/.venvs/rapid-mlx-0.16.1/bin/rapid-mlx"')
        self.assertIn("--backend rapid-mlx repair 0.16.1", r.stdout)


class RelocatedPinTest(unittest.TestCase):
    """0.58.0 Task 2: a pin at a MOVED venv (migration or manual reinstall moved it
    between ~/.venvs and the XDG root) resolves to the SAME version under the other
    root with a loud warning — the lane keeps running and the user is never gated.
    A different version is never substituted, and a valid pin is untouched+silent."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.home.mkdir()
        self.new_root = self.home / XDG_REL
        self.legacy = self.home / ".venvs"
        live_dir = self.home / ".local" / "share" / "free-agents" / "config"
        live_dir.mkdir(parents=True)
        self.live = live_dir / "config.local.sh"
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_DATA_HOME", "LA_USER_STATE_DIR", "LA_RAPID_BIN",
                            "LA_VENV", "_LA_RAPID_SYSTEM_CANDIDATES",
                            "LA_LIVE_CONFIG_DIR", "LA_CONFIG_DIR", "_LA_LIVE_FOR")}
        env["HOME"] = str(self.home)
        # bash >=4 required by config-lib's pre-existing associative arrays
        # (LA_CATALOGUE_*/LA_RETIRED; under /bin/bash 3.2 la_load_config dies at the
        # derived-catalogue assignment with "value too great for base"). No rapid-mlx
        # is reachable on this PATH, and every test here sets an explicit pin anyway.
        env["PATH"] = "/opt/homebrew/bin:/usr/bin:/bin"
        env["_LA_RAPID_SYSTEM_CANDIDATES"] = ""
        self.env = env

    def run_load(self, pin_line: str):
        """Write the live config with one pin line, run la_load_config, return
        ((LA_RAPID_BIN, LA_VENV), stderr) — stderr carries the loader's warnings."""
        self.live.write_text("# live\n" + pin_line, encoding="utf-8")
        r = subprocess.run(
            ["bash", "-c",
             'source config/config-lib.sh >/dev/null 2>&1; la_load_config >/dev/null; '
             'printf "%s" "${LA_RAPID_BIN-}|${LA_VENV-}"'],
            capture_output=True, text=True, cwd=REPO, env=self.env)
        self.assertEqual(r.returncode, 0, f"loader failed: {r.stderr}")
        return tuple(r.stdout.split("|")), r.stderr

    def test_moved_pin_resolves_to_same_version_in_new_root_and_warns(self):
        make_fake_venv(self.new_root, "rapid-mlx-1.0.0", "rapid-mlx")
        (rapid, _venv), err = self.run_load('LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-1.0.0/bin/rapid-mlx"\n')
        self.assertEqual(rapid, str(self.new_root / "rapid-mlx-1.0.0" / "bin" / "rapid-mlx"))
        self.assertIn("moved", err)
        self.assertIn("repair 1.0.0", err)

    def test_moved_pin_never_falls_back_to_another_version(self):
        make_fake_venv(self.new_root, "rapid-mlx-1.0.3", "rapid-mlx")
        (rapid, _venv), err = self.run_load('LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-1.0.0/bin/rapid-mlx"\n')
        # Only rapid-mlx-1.0.3 exists in the new root — a DIFFERENT version. The broken
        # pin is reported as today (value unchanged), never silently replaced by 1.0.3.
        self.assertEqual(rapid, str(self.legacy / "rapid-mlx-1.0.0" / "bin" / "rapid-mlx"))
        self.assertNotIn("using the same version", err)

    def test_moved_la_venv_resolves_the_same_way(self):
        make_fake_venv(self.new_root, "vllm-mlx-0.6.0", "vllm-mlx")
        (_rapid, venv), err = self.run_load('LA_VENV="$HOME/.venvs/vllm-mlx-0.6.0/bin"\n')
        self.assertEqual(venv, str(self.new_root / "vllm-mlx-0.6.0" / "bin"))
        self.assertIn("moved", err)

    def test_valid_pin_is_untouched_and_silent(self):
        make_fake_venv(self.new_root, "rapid-mlx-1.0.3", "rapid-mlx")
        pin = self.new_root / "rapid-mlx-1.0.3" / "bin" / "rapid-mlx"
        (rapid, _venv), err = self.run_load(f'LA_RAPID_BIN="{pin}"\n')
        self.assertEqual(rapid, str(pin))
        self.assertEqual(err, "")


class DetectBackendTest(unittest.TestCase):
    """0.58.0 Task 5: bin/la-detect-backend.py discovers rapid venvs in BOTH roots
    (new XDG root first, legacy ~/.venvs second) via managers.base.discovery_roots."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.home.mkdir()
        self.new_root = self.home / XDG_REL
        self.legacy = self.home / ".venvs"
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_DATA_HOME", "LA_USER_STATE_DIR", "LA_RAPID_BIN",
                            "LA_VENV", "_LA_RAPID_SYSTEM_CANDIDATES",
                            "LA_LIVE_CONFIG_DIR", "LA_CONFIG_DIR", "_LA_LIVE_FOR")}
        env["HOME"] = str(self.home)
        env["PATH"] = "/usr/bin:/bin"  # no rapid-mlx on PATH; brew paths absent on CI
        self.env = env
        spec = importlib.util.spec_from_file_location(
            "la_detect_backend", REPO / "bin" / "la-detect-backend.py")
        assert spec is not None and spec.loader is not None
        self.detect = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.detect)

    def discover(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            return self.detect._discover_rapid_bin()

    def test_detect_backend_sees_new_root_rapid(self):
        make_fake_venv(self.new_root, "rapid-mlx-1.0.3", "rapid-mlx")
        self.assertEqual(self.discover(),
                         str(self.new_root / "rapid-mlx-1.0.3" / "bin" / "rapid-mlx"))

    def test_detect_backend_still_sees_legacy_root_rapid(self):
        make_fake_venv(self.legacy, "rapid-mlx-1.0.0", "rapid-mlx")
        self.assertEqual(self.discover(),
                         str(self.legacy / "rapid-mlx-1.0.0" / "bin" / "rapid-mlx"))

    def test_detect_backend_prefers_new_root_on_a_version_tie(self):
        make_fake_venv(self.legacy, "rapid-mlx-1.0.3", "rapid-mlx")
        make_fake_venv(self.new_root, "rapid-mlx-1.0.3", "rapid-mlx")
        self.assertEqual(self.discover(),
                         str(self.new_root / "rapid-mlx-1.0.3" / "bin" / "rapid-mlx"))


def make_broken_venv(root: Path, dirname: str, binname: str) -> Path:
    """The measured broken-venv shape (2026-10-30): bin/<backend> is executable with a
    valid shebang, but bin/python is a DEAD symlink (the Homebrew keg was deleted). Only a
    health check that runs the interpreter catches it; X_OK alone cannot."""
    vdir = root / dirname
    (vdir / "bin").mkdir(parents=True)
    (vdir / "bin" / "python").symlink_to("/nonexistent/python-gone")
    script = vdir / "bin" / binname
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return vdir


class DetectBackendHealthParityTest(unittest.TestCase):
    """la-detect-backend.py _discover_rapid_bin must match the shell la_discover_rapid_bin:
    EVERY candidate passes la_backend_bin_health (venv_health in python), and *.repair-*
    aside dirs are never candidates (0.58.4 shell fix)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.home.mkdir()
        self.new_root = self.home / XDG_REL
        self.legacy = self.home / ".venvs"
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_DATA_HOME", "LA_USER_STATE_DIR", "LA_RAPID_BIN",
                            "LA_VENV", "_LA_RAPID_SYSTEM_CANDIDATES",
                            "LA_LIVE_CONFIG_DIR", "LA_CONFIG_DIR", "_LA_LIVE_FOR")}
        env["HOME"] = str(self.home)
        env["PATH"] = "/usr/bin:/bin"
        self.env = env
        spec = importlib.util.spec_from_file_location(
            "la_detect_backend", REPO / "bin" / "la-detect-backend.py")
        assert spec is not None and spec.loader is not None
        self.detect = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.detect)

    def discover(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            return self.detect._discover_rapid_bin()

    def test_broken_newest_venv_is_skipped_for_healthy_older(self):
        make_broken_venv(self.new_root, "rapid-mlx-9.0.0", "rapid-mlx")
        make_fake_venv(self.new_root, "rapid-mlx-2.0.0", "rapid-mlx")
        self.assertEqual(self.discover(),
                         str(self.new_root / "rapid-mlx-2.0.0" / "bin" / "rapid-mlx"))

    def test_repair_aside_dir_is_never_a_candidate(self):
        make_fake_venv(self.new_root, "rapid-mlx-0.16.10.repair-999", "rapid-mlx")
        self.assertIsNone(self.discover())

    def test_system_candidate_with_dead_interpreter_is_skipped(self):
        # real candidate shape: <prefix>/bin/rapid-mlx — the bin/ parent matters, because
        # _bin_healthy routes bin-dir paths through venv_health(root, name) (QC finding 1b)
        bad_root = Path(self.home) / "opt" / "homebrew"
        (bad_root / "bin").mkdir(parents=True)
        bad = bad_root / "bin" / "rapid-mlx"
        bad.write_text("#!/nonexistent/interp\nexit 0\n")
        bad.chmod(0o755)
        good_root = Path(self.home) / "usr" / "local"
        (good_root / "bin").mkdir(parents=True)
        good = good_root / "bin" / "rapid-mlx"
        good.write_text("#!/bin/sh\nexit 0\n")
        good.chmod(0o755)
        with mock.patch.object(self.detect, "_SYSTEM_CANDIDATES",
                               (str(bad), str(good))):
            self.assertEqual(self.discover(), str(good))

    def test_path_candidate_with_dead_interpreter_is_skipped(self):
        # shutil.which() candidate: a broken rapid-mlx on PATH must be skipped too (QC 1c)
        pdir = Path(self.home) / "pathbin"
        pdir.mkdir()
        bad = pdir / "rapid-mlx"
        bad.write_text("#!/nonexistent/interp\nexit 0\n")
        bad.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = f"{pdir}:/usr/bin:/bin"
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.detect, "_SYSTEM_CANDIDATES", ()):
            self.assertIsNone(self.detect._discover_rapid_bin())


if __name__ == "__main__":
    unittest.main()
