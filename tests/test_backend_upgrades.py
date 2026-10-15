#!/usr/bin/env python3
"""tests/test_backend_upgrades.py — find backend upgrades, ask yes/no, run the accepted ones.

User 2026-10-15: when an upgrade is available the user is asked once, yes/no, and a yes runs it with
no further steps. Each backend is upgraded by the tool that installed it (brew / pipx / versioned
venv). Every runner here is a stub: nothing is installed, nothing reaches the network.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))
import backend_upgrades as bu  # noqa: E402


def done(stdout="", rc=0):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


class FakeShell:
    """Answers the brew/pipx queries the module makes, from a small in-memory machine."""

    def __init__(self, brew=None, outdated=None, pipx_litellm=None):
        self.brew = brew or {}            # formula short name -> installed version
        self.outdated = outdated or []    # brew outdated --json formulae entries
        self.pipx_litellm = pipx_litellm  # installed litellm version under pipx, or None
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[:3] == ["brew", "list", "--versions"]:
            v = self.brew.get(cmd[3])
            return done(f"{cmd[3]} {v}\n", 0) if v else done("", 1)
        if cmd[:2] == ["brew", "outdated"]:
            return done(json.dumps({"formulae": self.outdated, "casks": []}))
        if cmd[:3] == ["pipx", "list", "--json"]:
            venvs = {}
            if self.pipx_litellm:
                venvs["litellm"] = {"metadata": {"main_package": {"package_version": self.pipx_litellm}}}
            return done(json.dumps({"venvs": venvs}))
        raise AssertionError(f"unexpected command {cmd}")


class FakeMgr:
    def __init__(self, installed, latest):
        self._i, self._l = installed, latest

    def get_installed_versions(self):
        return self._i

    def get_latest_version(self):
        return self._l


class Upgrades(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"
        p = mock.patch.dict(os.environ, {"LA_BACKEND_UPGRADES_STATE": str(self.state)})
        p.start()
        self.addCleanup(p.stop)

    # --- detection ----------------------------------------------------------------------
    def test_brew_routes_use_homebrews_own_verdict(self):
        sh = FakeShell(brew={"omlx": "0.7.0", "llama.cpp": "0.6.0_1"},
                       outdated=[{"name": "jundot/omlx/omlx", "installed_versions": ["0.7.0"],
                                  "current_version": "0.7.1rc1"}])
        ups = bu.brew_upgrades(sh)
        self.assertEqual([(u.backend, u.current, u.latest, u.command) for u in ups],
                         [("omlx", "0.7.0", "0.7.1rc1", ["brew", "upgrade", "jundot/omlx/omlx"])])

    def test_litellm_is_upgraded_through_pipx_so_injected_packages_survive(self):
        sh = FakeShell(pipx_litellm="1.105.1")
        ups = bu.pipx_upgrades(sh, latest=lambda p: "1.106.0")
        self.assertEqual(ups[0].command, ["pipx", "upgrade", "litellm"])
        self.assertEqual(bu.pipx_upgrades(sh, latest=lambda p: "1.105.1"), [])

    def test_backend_not_installed_through_its_route_is_never_offered(self):
        sh = FakeShell()   # no brew kegs, no pipx litellm
        self.assertEqual(bu.brew_upgrades(sh), [])
        self.assertEqual(bu.pipx_upgrades(sh, latest=lambda p: "9.9.9"), [])

    def test_venv_backend_offers_a_new_release_not_a_dependency_bump(self):
        ups = bu.venv_upgrades({"rapid-mlx": FakeMgr(["0.16.1", "0.15.7"], "0.17.0"),
                                "vllm-mlx": FakeMgr(["0.5.0"], "0.5.0")})
        self.assertEqual([(u.backend, u.latest) for u in ups], [("rapid-mlx", "0.17.0")])
        self.assertEqual(ups[0].command[-3:], ["rapid-mlx", "install", "0.17.0"])

    # --- prompting ----------------------------------------------------------------------
    def seed(self, *ups, declined=None):
        self.state.write_text(json.dumps({"pending": [bu.asdict(u) for u in ups],
                                          "declined": declined or {}}))

    def omlx(self):
        return bu.Upgrade("omlx", "0.7.0", "0.7.1rc1", ["brew", "upgrade", "jundot/omlx/omlx"], "homebrew")

    def test_yes_runs_the_command_and_clears_it(self):
        self.seed(self.omlx())
        ran = []
        rc = bu.prompt(False, ask=lambda q: "y", run_live=lambda c: ran.append(c) or 0,
                       run=FakeShell(brew={"omlx": "0.7.0"}), out=io.StringIO())
        self.assertEqual(rc, 0)
        self.assertEqual(ran, [["brew", "upgrade", "jundot/omlx/omlx"]])
        self.assertEqual(bu.pending(), [])

    def test_enter_means_yes(self):
        self.seed(self.omlx())
        ran = []
        bu.prompt(False, ask=lambda q: "", run_live=lambda c: ran.append(c) or 0,
                  run=FakeShell(brew={"omlx": "0.7.0"}), out=io.StringIO())
        self.assertEqual(len(ran), 1)

    def test_no_runs_nothing_and_is_remembered_for_that_version_only(self):
        self.seed(self.omlx())
        ran = []
        bu.prompt(False, ask=lambda q: "n", run_live=lambda c: ran.append(c) or 0,
                  run=FakeShell(brew={"omlx": "0.7.0"}), out=io.StringIO())
        self.assertEqual(ran, [])
        self.assertEqual(bu.pending(), [])            # not asked again for 0.7.1rc1
        newer = bu.Upgrade("omlx", "0.7.0", "0.7.2", ["brew", "upgrade", "jundot/omlx/omlx"], "homebrew")
        data = json.loads(self.state.read_text())
        data["pending"] = [bu.asdict(newer)]
        self.state.write_text(json.dumps(data))
        self.assertEqual([u.latest for u in bu.pending()], ["0.7.2"])   # a newer release is asked

    def test_failed_upgrade_stays_pending_and_reports_failure(self):
        self.seed(self.omlx())
        rc = bu.prompt(False, ask=lambda q: "y", run_live=lambda c: 1,
                       run=FakeShell(brew={"omlx": "0.7.0"}), out=io.StringIO())
        self.assertEqual(rc, 1)
        self.assertEqual([u.backend for u in bu.pending()], ["omlx"])

    def test_already_upgraded_elsewhere_is_not_asked(self):
        # The nightly brew/pipx job may have upgraded it since the weekly check.
        self.seed(self.omlx())
        asked = []
        bu.prompt(False, ask=lambda q: asked.append(q) or "y", run_live=lambda c: 0,
                  run=FakeShell(brew={"omlx": "0.7.1rc1"}), out=io.StringIO())
        self.assertEqual(asked, [])

    def test_nothing_pending_prints_nothing(self):
        out = io.StringIO()
        self.assertEqual(bu.prompt(False, ask=lambda q: "y", run_live=lambda c: 0,
                                   run=FakeShell(), out=out), 0)
        self.assertEqual(out.getvalue(), "")

    # --- CLI ----------------------------------------------------------------------------
    def cli(self, *args, env=None):
        e = dict(os.environ, LA_BACKEND_UPGRADES_STATE=str(self.state), **(env or {}))
        return subprocess.run([sys.executable, str(Path(bu.__file__)), *args], capture_output=True,
                              text=True, env=e, stdin=subprocess.DEVNULL, timeout=60)

    def test_prompt_without_a_terminal_refuses_instead_of_hanging(self):
        self.seed(self.omlx())
        r = self.cli("--prompt")
        self.assertEqual(r.returncode, 2)

    def test_opt_out_env_never_prompts(self):
        self.seed(self.omlx())
        self.assertEqual(self.cli("--prompt", env={"LA_BACKEND_UPGRADES": "0"}).returncode, 0)

    def test_list_reads_state_without_network(self):
        self.seed(self.omlx())
        r = self.cli("--list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("omlx\t0.7.0 -> 0.7.1rc1", r.stdout)


if __name__ == "__main__":
    unittest.main()
