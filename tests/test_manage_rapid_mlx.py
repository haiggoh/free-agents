#!/usr/bin/env python3
"""Tests for the manage-rapid-mlx.py wrapper (backward compatibility).

These tests verify that the original CLI functions are accessible via the
new RapidMLXManager class and the wrapper works correctly.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

# Import the RapidMLXManager class instead of the old module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
import managers.rapid_mlx as rapid_mlx_module
from managers.rapid_mlx import RapidMLXManager
from managers.base import ManagerError

# Also test the wrapper
MODULE_PATH = Path(__file__).resolve().parent.parent / "install" / "manage-rapid-mlx.py"
spec = importlib.util.spec_from_file_location("manage_rapid_mlx_wrapper", MODULE_PATH)
assert spec and spec.loader
manage_rapid_mlx = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = manage_rapid_mlx
spec.loader.exec_module(manage_rapid_mlx)

# Create a manager instance for testing
mgr = RapidMLXManager()
module = mgr  # Alias for compatibility with test code

passed = 0
failed: list[str] = []


def check(condition: bool, label: str) -> None:
    global passed
    if condition:
        passed += 1
        print(f"  PASS: {label}")
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


def raises(callable_, label: str) -> None:
    try:
        callable_()
    except ManagerError:
        check(True, label)
    except Exception as e:
        # Check if it's a ManagerError by name
        if type(e).__name__ == "ManagerError":
            check(True, label)
        else:
            check(False, f"{label} (wrong exception: {type(e).__name__})")
    else:
        check(False, label)


# create_repo points LA_LIVE_CONFIG_DIR at a per-sandbox dir. This file runs at pytest IMPORT time,
# so the original values are restored as the script's LAST act (below) — a mid-file cleanup was
# undone by a later create_repo call and leaked a deleted dir into the next test module.
_ORIGINAL_LIVE_ENV = {k: os.environ.get(k) for k in ("LA_LIVE_CONFIG_DIR", "_LA_LIVE_FOR")}


def create_repo(root: Path, private: str = "0.12.18") -> Path:
    """A free-agents-shaped tree plus a LIVE config pinning `private`. Since 0.49.0 a promotion
    writes ONLY the live config (LA_LIVE_CONFIG_DIR here, the machine state dir for real), so the
    repo needs no pin surfaces at all — just config-lib.sh to resolve the live file."""
    repo = root / "repo"
    (repo / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve().parents[1] / "config" / "config-lib.sh", repo / "config" / "config-lib.sh")
    live = root / "live"
    live.mkdir(exist_ok=True)
    (live / "config.local.sh").write_text(f'LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-{private}/bin/rapid-mlx"\n', encoding="utf-8")
    (live / "config.local.sh").chmod(0o600)
    os.environ["LA_LIVE_CONFIG_DIR"] = str(live)
    os.environ.pop("_LA_LIVE_FOR", None)
    return repo.resolve()


print("== versions and release selection ==")
check(module.version_key("0.14.0") > module.version_key("0.13.4"), "versions sort numerically")
check(module.version_key("0.14.0") > module.version_key("0.14.0rc1"), "stable sorts above prerelease")
raises(lambda: module.target_for("../../escape"), "unsafe version cannot escape managed root")
releases = {"0.14.0": [{"yanked": False}], "0.15.0rc1": [{"yanked": False}], "0.13.4": [{"yanked": True}], "bad": [{}]}
check(module.valid_versions(releases) == ["0.14.0"], "stable listing excludes prerelease/yanked/malformed")
check(module.valid_versions(releases, True) == ["0.15.0rc1", "0.14.0"], "prerelease listing is explicit")

print("== installed state and private receipts ==")
with tempfile.TemporaryDirectory() as temporary:
    home = Path(temporary)
    # Create manager with custom home
    test_mgr = RapidMLXManager(home=home)
    root = test_mgr.venv_root
    root.mkdir()
    complete = root / "rapid-mlx-0.14.0"
    (complete / "bin").mkdir(parents=True)
    binary = complete / "bin" / "rapid-mlx"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    (root / "rapid-mlx-0.13.4").mkdir()
    entries = test_mgr.installed_versions()
    check(entries[0][0] == "0.14.0" and entries[0][2] == "complete", "complete environment detected")
    check(entries[1][2] == "incomplete", "incomplete environment stays unhealthy")
    receipt = home / "receipt.json"
    test_mgr.atomic_json(receipt, {"ok": True})
    check(stat.S_IMODE(receipt.stat().st_mode) == 0o600, "receipt mode is private")
    lock_path = test_mgr.receipt_path("0.14.0")
    test_mgr.atomic_json(lock_path, {"version": "0.14.0", "pip_freeze": ["rapid-mlx==0.14.0", "mlx==0.32.2"]})
    check(test_mgr.locked_requirements("0.14.0") is not None, "valid recreation lock recovered")
    test_mgr.atomic_json(lock_path, {"version": "0.14.0", "pip_freeze": ["mlx==0.32.2"]})
    check(test_mgr.locked_requirements("0.14.0") is None, "receipt without Rapid pin rejected")

print("== pin planning targets ONLY the live config ==")
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = create_repo(root)
    plan = module.plan_pin_update(repo, "0.14.0")
    changes = plan[1]
    check([item[0] for item in changes] == [root / "live" / "config.local.sh"], "the live config is the only planned file")
    check(all(b"0.14.0" in item[2] and b"0.12.18" not in item[2] for item in changes), "planned output moves the pin")
    before = {item[0]: item[0].read_bytes() for item in changes}
    result = module.apply_pin_plan(plan, dry_run=True)
    check(result["result"] == "dry_run" and all(path.read_bytes() == data for path, data in before.items()), "pin dry-run writes nothing")

print("== transactional promotion and private mode ==")
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = create_repo(root)
    test_mgr = RapidMLXManager(repo=repo)
    plan = test_mgr.plan_pin_update(repo, "0.14.0")
    transaction = root / "transaction"
    result = test_mgr.apply_pin_plan(plan, validator=None, backup_root=transaction)
    live = root / "live" / "config.local.sh"
    check(result["result"] == "promoted", "pin transaction reports promotion")
    check(b"0.14.0" in live.read_bytes(), "pin transaction writes the live config")
    check(stat.S_IMODE(live.stat().st_mode) == 0o600, "live config remains mode 600")
    check((transaction / "manifest.json").is_file(), "transaction manifest retained")
    check(not test_mgr.plan_pin_update(repo, "0.14.0")[1], "second promotion is an idempotent no-op")

print("== a failing validator rolls the live config back ==")
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = create_repo(root)
    test_mgr = RapidMLXManager(repo=repo)
    plan = test_mgr.plan_pin_update(repo, "0.14.0")
    originals = {item[0]: item[1] for item in plan[1]}

    def failing_validator(_repo, _version):
        raise subprocess.CalledProcessError(1, ["bash", "-c", "la_backend_bin_health"])
    try:
        test_mgr.apply_pin_plan(plan, validator=failing_validator, backup_root=root / "tx")
        raised = False
    except ManagerError as exc:
        raised = "rolled back" in str(exc)
    check(raised, "a validator failure surfaces as a rolled-back ManagerError")
    check(all(path.read_bytes() == data for path, data in originals.items()),
          "the live config is restored byte-for-byte after a failed validation")

print("== unsafe or pin-less live configs fail closed / stay untouched ==")
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = create_repo(root)
    live = root / "live" / "config.local.sh"
    live.write_text("# no Rapid pin: auto-discovery\n", encoding="utf-8")
    plan = module.plan_pin_update(repo, "0.14.0")
    check(not plan[1] and plan[3] == "no_explicit_pin", "a live config without a pin line is never given one")
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = create_repo(root)
    live = root / "live" / "config.local.sh"
    real = root / "elsewhere.sh"
    real.write_text(live.read_text(encoding="utf-8"), encoding="utf-8")
    live.unlink()
    live.symlink_to(real)
    raises(lambda: module.plan_pin_update(repo, "0.14.0"), "symlinked live config rejected")

print("== install dry-run and skip-pin semantics ==")
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = create_repo(root)
    home = root / "home"
    test_mgr = RapidMLXManager(home=home)
    receipt = test_mgr.install_version(
        "0.14.1", python=None, refresh_deps=False, dry_run=True,
    )
    check(receipt.metadata.get("installation_mode") in ("locked_recreation", "fresh_resolution"), "dry-run is explicit")
    check(not (home / ".venvs").exists() and not test_mgr.receipt_path("0.14.1").exists(), "install dry-run creates no venv or receipt")
    # Test skip-pin is handled by the wrapper, not the manager directly

print("== wrapper help and command surface ==")
# Test the wrapper's help
helped = subprocess.run([sys.executable, str(MODULE_PATH), "--help"], text=True, capture_output=True)
check(helped.returncode == 0 and "releases" in helped.stdout, "--help still documents the subcommands")
# The legacy wrapper must hand the unified CLI an explicit --backend rapid-mlx. Run it with a
# subcommand that only manage-backend.py knows and that needs --backend, and check it worked.
listed = subprocess.run([sys.executable, str(MODULE_PATH), "installed"], text=True, capture_output=True,
                        env={**os.environ, "HOME": tempfile.mkdtemp()})
check(listed.returncode == 0 and "No versioned rapid-mlx environments found" in listed.stdout,
      "wrapper translates backend argument (installed runs as --backend rapid-mlx)")

for _key, _value in _ORIGINAL_LIVE_ENV.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value

print(f"\n{passed} passed, {len(failed)} failed")
for label in failed:
    print(f"  FAILED: {label}")
if __name__ == "__main__":
    raise SystemExit(1 if failed else 0)