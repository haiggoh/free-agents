"""tests/test_promote_normalizes_keg_0510.py — 0.51.0 item C, ROOT CAUSE: promoting a pin must
never point at a Homebrew patch-keg venv — a keg-based venv breaks at the next Python patch
upgrade, which is exactly how About ended up showing 'pinned 1.0.0' next to '⚠ pinned-keg
0.16.10' (two version numbers that look like racing). promote_pins now normalizes the target
venv onto the patch-stable opt/ path first, and refuses to pin a venv that cannot be
normalized. The ⚠ flag stays for genuinely broken venvs (user 2026-11-03: warning about a real
problem is correct; the fix is that pins never land on keg venvs anymore)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "install"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import managers.rapid_mlx  # noqa: E402,F401  (registers the manager)
from managers.base import ManagerError  # noqa: E402
from managers.durability import venv_durability  # noqa: E402
from managers.rapid_mlx import RapidMLXManager  # noqa: E402
from test_venv_durability import FakeBrew, keg_based_venv  # noqa: E402

import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive  # builds REAL venvs in temp dirs (slow under load)

PIN_LINE = 'LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-{v}/bin/rapid-mlx"\n'


def make_rapid_venv(home: Path, version: str) -> Path:
    target = home / ".venvs" / f"rapid-mlx-{version}"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / "rapid-mlx"
    script.write_text(f'#!{target}/bin/python\nprint("rapid-mlx {version}")\n', encoding="utf-8")
    script.chmod(0o755)
    return target


class PromoteNormalizesKeg(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.repo = root / "repo"
        shutil.copytree(REPO / "config", self.repo / "config",
                        ignore=shutil.ignore_patterns("config.local.sh", "backups"))
        (self.repo / "bin").mkdir()
        (self.repo / "bin" / "README").write_text("tracked placeholder\n", encoding="utf-8")
        self.live_dir = self.home / ".local" / "share" / "free-agents" / "config"
        self.live_dir.mkdir(parents=True)
        self.live = self.live_dir / "config.local.sh"
        self.live.write_text("# live machine config\n" + PIN_LINE.format(v="0.1.0"), encoding="utf-8")
        self.live.chmod(0o600)
        make_rapid_venv(self.home, "0.1.0")
        self.target = make_rapid_venv(self.home, "0.2.0")
        self.brew = FakeBrew(root / "brew")
        self.brew.install("3.14.8_2")
        keg_based_venv(self.target, self.brew, "3.14.8_2")
        env = {k: v for k, v in os.environ.items()
               if k not in ("LA_LIVE_CONFIG_DIR", "_LA_LIVE_FOR", "LA_CONFIG_DIR", "LA_RAPID_BIN",
                            "HOMEBREW_PREFIX")}
        env["HOME"] = str(self.home)
        env["HOMEBREW_PREFIX"] = str(self.brew.root)   # durability only recognises real prefixes
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "fixture"], check=True)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def manager(self):
        mgr = RapidMLXManager(home=self.home, repo=self.repo)
        mgr.validate_environment = lambda version: None  # pip check needs pip; not under test here
        return mgr

    def test_promote_normalizes_a_keg_venv_before_pinning(self):
        self.assertEqual(venv_durability(self.target), "pinned-keg")
        self.manager().promote_pins("0.2.0")
        self.assertEqual(venv_durability(self.target), "durable",
                         "promote must normalize the keg base onto opt/ first — otherwise the pin "
                         "points at a venv that breaks on the next Python patch (the drift)")
        self.assertIn("0.2.0", self.live.read_text(), "the live pin must move to the promoted version")

    def test_promote_refuses_a_venv_that_cannot_be_normalized(self):
        self.brew.opt.unlink()  # opt/ gone -> normalize cannot complete -> must refuse, not pin
        with self.assertRaisesRegex(ManagerError, "refusing to pin"):
            self.manager().promote_pins("0.2.0")
        self.assertIn("0.1.0", self.live.read_text(), "a refused pin must leave the live pin unmoved")
        self.assertEqual(venv_durability(self.target), "pinned-keg",
                         "the failed normalize must restore the original keg state byte-for-byte")


class PromoteViaCliNormalizesKeg(PromoteNormalizesKeg):
    """The REAL entry path: install/manage-backend.py ... promote — what the picker's
    "Promote pins" row runs. The 0.52.0 tests drove mgr.promote_pins() directly, which is how
    a paper-only changelog claim happens; this drives the CLI end to end."""

    def setUp(self):
        super().setUp()
        # The real CLI validates with `pip check`, which a --without-pip venv fails — so the
        # 0.2.0 fixture venv is rebuilt WITH pip and keg-based again (same shape as the
        # fragile 0.16.10 on the real machine).
        import shutil
        from test_venv_durability import EXE
        shutil.rmtree(self.target)
        subprocess.run([sys.executable, "-m", "venv", str(self.target)], check=True)
        script = self.target / "bin" / "rapid-mlx"
        script.write_text(f'#!{self.target}/bin/python\nprint("rapid-mlx 0.2.0")\n')
        script.chmod(0o755)
        fw = self.brew.framework_bin("3.14.8_2")
        for name in os.listdir(self.target / "bin"):
            link = self.target / "bin" / name
            if link.is_symlink() and (name == EXE or not os.readlink(link).startswith("python")):
                link.unlink()
                link.symlink_to(fw / EXE)
        cfg = self.target / "pyvenv.cfg"
        lines = [ln for ln in cfg.read_text().splitlines() if not ln.startswith(("home =", "executable ="))]
        cfg.write_text("\n".join([f"home = {fw}", f"executable = {fw / EXE}", *lines]) + "\n")
        assert venv_durability(self.target) == "pinned-keg"

    def test_manage_backend_cli_promote_normalizes_the_keg_venv(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["HOMEBREW_PREFIX"] = str(self.brew.root)
        for k in ("LA_LIVE_CONFIG_DIR", "_LA_LIVE_FOR", "LA_CONFIG_DIR", "LA_RAPID_BIN"):
            env.pop(k, None)
        r = subprocess.run([sys.executable, str(REPO / "install" / "manage-backend.py"),
                            "--home", str(self.home), "--backend", "rapid-mlx",
                            "promote", "0.2.0", "--repo", str(self.repo)],
                           capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        self.assertEqual(venv_durability(self.target), "durable",
                         "CLI promote must normalize the keg venv onto opt/ first")
        self.assertIn("0.2.0", self.live.read_text(), "the live pin must move")


if __name__ == "__main__":
    unittest.main()
