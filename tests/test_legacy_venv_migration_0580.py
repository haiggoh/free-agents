#!/usr/bin/env python3
"""Legacy-venv migration offers (0.58.0 plan Task 4).

The launch prompt (install/backend_upgrades.py prompt()) gains a second offer source,
install/legacy_venv_migration.py: one offer per managed legacy venv
(~/.venvs/<prefix>-<ver>, prefixes of managers with SUPPORTS_VENV_REMOVE) that has NO
same-name dir in the new XDG root. The offer runs the backend manager's own
`repair <ver>` (make-before-break, re-pins a live pin — Task 3). Unmanaged ~/.venvs/*
entries are never listed, offered or touched; a version present in BOTH roots gets no
offer; a declined migration is remembered per venv but a NEW legacy venv is still asked.
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "install"))

import backend_upgrades as bu  # noqa: E402
import legacy_venv_migration as lvm  # noqa: E402

XDG_REL = Path(".local") / "share" / "free-agents" / "backends"


class MigrationHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.legacy = self.home / ".venvs"
        self.new_root = self.home / XDG_REL
        self.state = root / "upgrades.json"
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_DATA_HOME", "LA_LIVE_CONFIG_DIR", "_LA_LIVE_FOR",
                            "LA_RAPID_BIN", "LA_VENV", "LA_BACKEND_UPGRADES_STATE",
                            "LA_USER_STATE_DIR")}
        env["HOME"] = str(self.home)
        env["LA_BACKEND_UPGRADES_STATE"] = str(self.state)
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def mkdir(self, root: Path, name: str) -> Path:
        d = root / name
        d.mkdir(parents=True)
        return d

    def write_state(self, data: dict) -> None:
        self.state.write_text(json.dumps(data), encoding="utf-8")

    def read_state(self) -> dict:
        return json.loads(self.state.read_text(encoding="utf-8"))


class OffersTest(MigrationHome):
    def test_offers_list_only_legacy_only_managed_venvs(self):
        self.mkdir(self.legacy, "rapid-mlx-1.0.0")
        self.mkdir(self.legacy, "rapid-mlx-foo")          # unmanaged: never offered
        self.mkdir(self.legacy, "vllm-mlx-0.6.0")
        self.mkdir(self.new_root, "rapid-mlx-1.0.0")      # in BOTH roots: new root wins, no offer
        self.mkdir(self.new_root, "litellm-9.9.9")        # new root only: nothing to migrate
        offers = lvm.offers()
        self.assertEqual([(u.backend, u.current, u.via) for u in offers],
                         [("vllm-mlx", "0.6.0", "migrate")])
        u = offers[0]
        self.assertEqual(u.latest, "0.6.0")
        self.assertEqual(u.command[-2:], ["repair", "0.6.0"])
        self.assertIn("manage-backend.py", u.command[1])


class PromptTest(MigrationHome):
    def setUp(self):
        super().setUp()
        self.write_state({"notify": True, "pending": [], "declined": {}})

    def run_prompt(self, ask, run_live, assume_yes=False):
        out = io.StringIO()
        asks = []
        rcs = []

        def ask_stub(q):
            asks.append(q)
            return ask(q)

        def live_stub(cmd):
            rcs.append(list(cmd))
            return run_live(cmd)

        rc = bu.prompt(assume_yes, ask=ask_stub, run_live=live_stub,
                       run=lambda cmd: subprocess.CompletedProcess(cmd, 1, "", ""),
                       out=out, sources=(lvm.offers,))
        return rc, out.getvalue(), asks, rcs

    def test_prompt_offers_migration_and_runs_repair_on_yes(self):
        self.mkdir(self.legacy, "rapid-mlx-1.0.0")
        rc, out, asks, rcs = self.run_prompt(lambda q: "y", lambda cmd: 0)
        self.assertEqual(rc, 0)
        self.assertEqual(len(rcs), 1)
        self.assertEqual(rcs[0][-2:], ["repair", "1.0.0"])
        self.assertIn("rapid-mlx", rcs[0])
        self.assertIn("Migrate rapid-mlx 1.0.0", asks[0])
        self.assertIn("in the old location", out)

    def test_declined_migration_is_not_reasked_but_a_new_one_is(self):
        self.mkdir(self.legacy, "rapid-mlx-1.0.0")
        self.mkdir(self.legacy, "rapid-mlx-1.0.2")
        self.write_state({"notify": True, "pending": [],
                          "declined": {"migrate:rapid-mlx-1.0.0": "1.0.0"}})
        rc, out, asks, rcs = self.run_prompt(lambda q: "n", lambda cmd: 0)
        self.assertEqual(len(asks), 1, f"only the undeclined venv is asked: {asks}")
        self.assertIn("1.0.2", asks[0])
        self.assertNotIn("1.0.0?", asks[0])
        self.assertIn("skipped", out)
        self.assertEqual(self.read_state()["declined"].get("migrate:rapid-mlx-1.0.2"), "1.0.2")
        self.assertEqual(rcs, [])

    def test_migration_header_is_separate_from_the_upgrade_notice(self):
        self.mkdir(self.legacy, "rapid-mlx-1.0.0")
        rc, out, asks, rcs = self.run_prompt(lambda q: "n", lambda cmd: 0)
        self.assertIn("in the old location", out)
        self.assertNotIn(bu.UPGRADE_NOTICE, out)

    def test_notify_off_prints_nothing(self):
        self.mkdir(self.legacy, "rapid-mlx-1.0.0")
        self.write_state({"notify": False, "pending": [], "declined": {}})
        rc, out, asks, rcs = self.run_prompt(lambda q: "y", lambda cmd: 0)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertEqual(asks, [])
        self.assertEqual(rcs, [])


class ModuleCliTest(MigrationHome):
    def test_module_help_runs(self):
        r = subprocess.run([sys.executable, str(REPO / "install" / "legacy_venv_migration.py"), "--help"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--list", r.stdout)

    def test_module_list_json(self):
        self.mkdir(self.legacy, "rapid-mlx-1.0.0")
        r = subprocess.run([sys.executable, str(REPO / "install" / "legacy_venv_migration.py"),
                            "--list", "--json"],
                           capture_output=True, text=True, env={**os.environ})
        self.assertEqual(r.returncode, 0, r.stderr)
        items = json.loads(r.stdout)
        self.assertEqual([(i["backend"], i["current"], i["via"]) for i in items],
                         [("rapid-mlx", "1.0.0", "migrate")])


if __name__ == "__main__":
    unittest.main()
