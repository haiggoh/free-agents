#!/usr/bin/env python3
"""Tests for bin/fa_pre_trust.py — the ONE writer of the sanctioned trust key (0.59.0).

Task 2 of docs/superpowers/plans/2026-11-15-handover-pre-trust.md. Every test uses a
temp config + temp HOME; the autouse guard asserts the REAL ~/.claude.json is untouched
(exists/mtime before == after, or absent). Key truth comes from the Task 1 probe:
realpath spelling only; worktree OR main-checkout key suppresses; CLAUDE_CONFIG_DIR
honored when set.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("fa_pre_trust", ROOT / "bin" / "fa_pre_trust.py")
assert _SPEC is not None and _SPEC.loader is not None
fa_pre_trust = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fa_pre_trust)

HELPER = ROOT / "bin" / "fa_pre_trust.py"
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from hermetic_env import hermetic_env  # noqa: E402

REAL_CLAUDE_JSON = os.path.realpath(os.path.expanduser("~/.claude.json"))


def _real_guard_fingerprint():
    """Fingerprint of what a TEST could write into the real ~/.claude.json: the
    hasTrustDialogAccepted values of projects keys under the pytest TMPDIR. Live
    sessions on this machine legitimately rewrite the file (numStartups etc.), so a
    raw mtime equality flakes under -n 12 (measured 2026-11-15); they never create
    TMPDIR-prefixed keys, so this catches exactly the test-side write."""
    if not os.path.exists(REAL_CLAUDE_JSON):
        return None
    with open(REAL_CLAUDE_JSON, "rb") as f:
        d = json.load(f)
    tmp = os.path.realpath(os.environ.get("TMPDIR", "/tmp"))
    proj = d.get("projects", {})
    return tuple(sorted((k, v.get("hasTrustDialogAccepted"))
                        for k, v in proj.items()
                        if isinstance(v, dict) and (k.startswith(tmp) or k.startswith("/private" + tmp))))


def run_cli(*args, extra_env=None):
    env = hermetic_env()
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update({k: v for k, v in extra_env.items() if v is not None})
    return subprocess.run([sys.executable, str(HELPER), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def plant(cfg: Path, data) -> bytes:
    raw = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    cfg.write_bytes(raw)
    return raw


def load(cfg: Path):
    return json.loads(cfg.read_bytes().decode("utf-8"))


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@local", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "commit", "--allow-empty", "-q", "-m", "init")
    return path


@pytest.fixture(autouse=True)
def speed_and_real_guard(monkeypatch):
    monkeypatch.setattr(fa_pre_trust, "VERIFY_DELAY_S", 0.0)
    before = _real_guard_fingerprint()
    yield
    assert _real_guard_fingerprint() == before, \
        "a test wrote a trust key into the real ~/.claude.json"


@pytest.fixture
def sb(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cfg = tmp_path / "claude.json"
    target = tmp_path / "target"
    target.mkdir()
    return SimpleNamespace(home=home, cfg=cfg, target=target, tmp=tmp_path)


def test_untrusted_plain_repo_gets_key(sb):
    make_repo(sb.target)
    plant(sb.cfg, {"projects": {}})
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 0 and res.verified and res.wrote
    key = os.path.realpath(sb.target)
    assert load(sb.cfg)["projects"][key]["hasTrustDialogAccepted"] is True


def test_worktree_writes_the_proven_keys(sb):
    repo = make_repo(sb.tmp / "repo")
    wt = sb.tmp / "repo-wt"
    git(repo, "worktree", "add", "-q", str(wt), "HEAD")
    plant(sb.cfg, {"projects": {}})
    res = fa_pre_trust.pre_trust(str(wt), config_path=str(sb.cfg))
    keys = set(res.keys)
    assert keys == {os.path.realpath(wt), os.path.realpath(repo)}
    assert res.exit_code == 0 and res.verified
    proj = load(sb.cfg)["projects"]
    for k in keys:
        assert proj[k]["hasTrustDialogAccepted"] is True


def test_subdirectory_target_writes_repo_root_and_itself(sb):
    repo = make_repo(sb.tmp / "repo")
    sub = repo / "sub"
    sub.mkdir()
    plant(sb.cfg, {"projects": {}})
    res = fa_pre_trust.pre_trust(str(sub), config_path=str(sb.cfg))
    assert set(res.keys) == {os.path.realpath(sub), os.path.realpath(repo)}
    assert res.exit_code == 0


def test_already_trusted_is_a_noop(sb):
    key = os.path.realpath(sb.target)
    plant(sb.cfg, {"projects": {key: {"hasTrustDialogAccepted": True}}})
    before = sb.cfg.read_bytes()
    mtime = sb.cfg.stat().st_mtime_ns
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 0 and res.already and not res.wrote and res.verified
    assert sb.cfg.read_bytes() == before
    assert sb.cfg.stat().st_mtime_ns == mtime
    assert list(sb.cfg.parent.glob("*.bak.pre-trust-*")) == []


def test_other_keys_untouched(sb):
    target_key = os.path.realpath(sb.target)
    data = {
        "oauthAccount": {"accessToken": "sk-secret-abc123", "email": "x@example.com"},
        "projects": {
            "/some/other/project": {"hasTrustDialogAccepted": False},
            "/home/user/projet-é": {"allowedTools": {"Bash": True}},
        },
        "customTopLevel": {"nested": [1, 2, 3]},
    }
    raw = plant(sb.cfg, data)
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 0
    before = json.loads(raw.decode("utf-8"))
    after = load(sb.cfg)
    # key ORDER preserved at both levels
    assert list(after.keys()) == list(before.keys())
    assert list(after["projects"].keys()) == list(before["projects"].keys()) + [target_key]
    # everything equal except the one new project entry
    after["projects"].pop(target_key)
    assert after == before


def test_existing_project_entry_keeps_its_fields(sb):
    key = os.path.realpath(sb.target)
    plant(sb.cfg, {"projects": {key: {"allowedTools": {"Bash": True}, "hasTrustDialogAccepted": False}}})
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 0
    entry = load(sb.cfg)["projects"][key]
    assert entry["allowedTools"] == {"Bash": True}
    assert entry["hasTrustDialogAccepted"] is True


def test_home_folder_is_exit_3_and_no_write(sb):
    plant(sb.cfg, {"projects": {}})
    before = sb.cfg.read_bytes()
    res = fa_pre_trust.pre_trust(str(sb.home), config_path=str(sb.cfg))
    assert res.exit_code == 3 and not res.wrote
    assert sb.cfg.read_bytes() == before
    assert list(sb.cfg.parent.glob("*.bak.pre-trust-*")) == []


def test_not_a_directory_exit_3(sb):
    plant(sb.cfg, {"projects": {}})
    before = sb.cfg.read_bytes()
    res = fa_pre_trust.pre_trust(str(sb.tmp / "nope"), config_path=str(sb.cfg))
    assert res.exit_code == 3
    assert sb.cfg.read_bytes() == before


def test_missing_explicit_config_exit_2(sb):
    missing = sb.tmp / "missing.json"
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(missing))
    assert res.exit_code == 2 and not missing.exists()
    cli = run_cli(str(sb.target), "--config", str(missing))
    assert cli.returncode == 2 and "missing" in cli.stderr.lower()


def test_unparseable_config_exit_4_untouched(sb):
    sb.cfg.write_bytes(b"{not json at all")
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 4 and not res.verified
    assert sb.cfg.read_bytes() == b"{not json at all"
    assert list(sb.cfg.parent.glob("*.bak.pre-trust-*")) == []


def test_lost_write_detected_exit_4(sb, monkeypatch):
    plant(sb.cfg, {"projects": {}})
    original = fa_pre_trust._load_config
    calls = {"n": 0}

    def fake_load(cfg):
        calls["n"] += 1
        if calls["n"] >= 2:  # the post-write verify read: pretend the key is gone
            return {"projects": {}}
        return original(cfg)

    monkeypatch.setattr(fa_pre_trust, "_load_config", fake_load)
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 4 and not res.verified and res.wrote
    # the write itself did land (only the verify failed)
    key = os.path.realpath(sb.target)
    assert load(sb.cfg)["projects"][key]["hasTrustDialogAccepted"] is True


def test_backup_created_mode_600_and_rotated_to_5(sb):
    plant(sb.cfg, {"projects": {}})
    for i in range(6):  # pre-existing backups, oldest first
        b = sb.cfg.parent / f"{sb.cfg.name}.bak.pre-trust-2026010{i}T000000Z"
        b.write_bytes(b"old")
        os.utime(b, (1000 + i, 1000 + i))
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 0 and res.backup
    backups = sorted(sb.cfg.parent.glob(f"{sb.cfg.name}.bak.pre-trust-*"))
    assert len(backups) == 5
    new = sb.cfg.parent / res.backup if not os.path.isabs(res.backup) else Path(res.backup)
    assert new.exists() and (new.stat().st_mode & 0o777) == 0o600
    names = [b.name for b in backups]
    assert "2026010000T000000Z" not in "".join(names)  # oldest rotated out
    assert os.path.basename(res.backup) in names


def test_mode_preserved(sb):
    plant(sb.cfg, {"projects": {}})
    sb.cfg.chmod(0o640)
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 0
    assert (sb.cfg.stat().st_mode & 0o777) == 0o640


def test_dry_run_writes_nothing(sb):
    plant(sb.cfg, {"projects": {}})
    before = sb.cfg.read_bytes()
    mtime = sb.cfg.stat().st_mtime_ns
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg), dry_run=True)
    assert res.exit_code == 0 and not res.wrote and res.keys
    assert sb.cfg.read_bytes() == before
    assert sb.cfg.stat().st_mtime_ns == mtime
    assert list(sb.cfg.parent.glob("*.bak.pre-trust-*")) == []


def test_json_output_never_contains_config_values(sb):
    plant(sb.cfg, {"oauthAccount": {"accessToken": "sk-secret-abc123"}, "projects": {}})
    cli = run_cli(str(sb.target), "--config", str(sb.cfg), "--json")
    assert cli.returncode == 0, cli.stderr
    out = json.loads(cli.stdout)
    assert set(out.keys()) == {"target", "keys", "already", "wrote", "verified", "backup", "reason"}
    assert "sk-secret-abc123" not in cli.stdout
    assert "sk-secret-abc123" not in cli.stderr


def test_help_runs_nothing(sb):
    plant(sb.cfg, {"projects": {}})
    before = sb.cfg.read_bytes()
    mtime = sb.cfg.stat().st_mtime_ns
    cli = run_cli("--help")
    assert cli.returncode == 0 and "usage" in cli.stdout.lower()
    cli2 = run_cli(str(sb.target), "--config", str(sb.cfg), "--help")
    assert cli2.returncode == 0
    assert sb.cfg.read_bytes() == before and sb.cfg.stat().st_mtime_ns == mtime


def test_unknown_flag_exit_2(sb):
    cli = run_cli(str(sb.target), "--bogus-flag")
    assert cli.returncode == 2
    assert "usage" in cli.stderr.lower()


def test_real_claude_json_untouched(sb):
    plant(sb.cfg, {"projects": {}})
    before = _real_guard_fingerprint()
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 0
    assert _real_guard_fingerprint() == before


def test_claude_config_dir_resolution(sb, monkeypatch):
    cfgdir = sb.tmp / "cfgdir"
    cfgdir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfgdir))
    res = fa_pre_trust.pre_trust(str(sb.target))  # no --config: env resolution
    assert res.exit_code == 0
    key = os.path.realpath(sb.target)
    assert load(cfgdir / ".claude.json")["projects"][key]["hasTrustDialogAccepted"] is True
    assert not sb.cfg.exists()  # the ~/.claude.json default was NOT used


# ---- review fix pass (Task 6 findings, verified against the code) --------------------

def test_bare_repo_worktree_never_keys_the_parent(sb):
    make_repo(sb.tmp / "seed")
    git(sb.tmp, "clone", "-q", "--bare", "seed", "bare.git")
    wt = sb.tmp / "wt"
    git(sb.tmp / "bare.git", "worktree", "add", "-q", str(wt), "HEAD")
    keys = fa_pre_trust.resolve_keys(str(wt))
    # a bare repo's common dir is the bare repo itself — its PARENT must not be keyed
    assert keys == [os.path.realpath(wt)], keys
    assert os.path.realpath(sb.tmp) not in keys


def test_home_rooted_repo_never_keys_home(sb):
    git(sb.home, "init", "-q")  # dotfiles-style: .git directly in HOME
    scratch = sb.home / "scratch"
    scratch.mkdir()
    home_rp = os.path.realpath(sb.home)
    keys = fa_pre_trust.resolve_keys(str(scratch))
    assert home_rp not in keys, keys
    plant(sb.cfg, {"projects": {}})
    res = fa_pre_trust.pre_trust(str(scratch), config_path=str(sb.cfg))
    assert res.exit_code == 0
    assert home_rp not in load(sb.cfg)["projects"]


def test_rotation_keeps_only_own_timestamped_backups(sb):
    plant(sb.cfg, {"projects": {}})
    keepers = []
    for tag in ("probe-20260101T000000Z", "dogfood-20260101T000000Z"):
        b = sb.cfg.parent / f"{sb.cfg.name}.bak.pre-trust-{tag}"
        b.write_bytes(b"x")
        keepers.append(b)
    for i in range(6):
        b = sb.cfg.parent / f"{sb.cfg.name}.bak.pre-trust-2026010{i}T000000Z"
        b.write_bytes(b"old")
        os.utime(b, (1000 + i, 1000 + i))
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 0
    names = {b.name for b in sb.cfg.parent.glob(f"{sb.cfg.name}.bak.pre-trust-*")}
    assert all(k.name in names for k in keepers), "probe/dogfood receipt backups must survive"
    stamps = [n for n in names
              if re.match(r"^.*\.bak\.pre-trust-\d{8}T\d{6}Z(-\d+)?$", n)]
    assert len(stamps) == 5, stamps


def test_backup_name_collision_is_suffixed_not_crash(sb):
    plant(sb.cfg, {"projects": {}})
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    taken = sb.cfg.parent / f"{sb.cfg.name}.bak.pre-trust-{stamp}"
    taken.write_bytes(b"taken")
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 0, res.reason
    assert res.backup and os.path.isfile(res.backup)
    assert res.backup != str(taken)


def test_tmp_file_created_with_target_mode(sb, monkeypatch):
    plant(sb.cfg, {"projects": {}})
    sb.cfg.chmod(0o640)
    seen = {}
    real_chmod = os.chmod

    def spy(path, mode):
        # the race window is BETWEEN file creation and chmod: record the mode AT chmod time
        seen["mode_at_chmod"] = os.stat(path).st_mode & 0o777
        return real_chmod(path, mode)

    monkeypatch.setattr(fa_pre_trust.os, "chmod", spy)
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(sb.cfg))
    assert res.exit_code == 0
    # post-fix the tmp is created via os.open(mode=...) and chmod is never called;
    # pre-fix chmod runs on a umask-created (world-readable) tmp — that is the bug.
    assert seen.get("mode_at_chmod") in (None, 0o640), \
        f"tmp was world-readable before chmod: {oct(seen.get('mode_at_chmod', 0))}"


def test_fresh_config_creates_file_and_reports_no_backup(sb, monkeypatch):
    cfgdir = sb.tmp / "fresh"
    cfgdir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfgdir))
    res = fa_pre_trust.pre_trust(str(sb.target))
    assert res.exit_code == 0
    assert res.backup is None, "no pre-existing file -> no backup, and the field says so"
    key = os.path.realpath(sb.target)
    assert load(cfgdir / ".claude.json")["projects"][key]["hasTrustDialogAccepted"] is True


def test_symlinked_config_writes_through_and_survives(sb):
    real = sb.tmp / "real.json"
    plant(real, {"projects": {}})
    link = sb.tmp / "link.json"
    link.symlink_to(real)
    res = fa_pre_trust.pre_trust(str(sb.target), config_path=str(link))
    assert res.exit_code == 0
    assert link.is_symlink(), "os.replace must not replace the symlink with a regular file"
    key = os.path.realpath(sb.target)
    assert load(real)["projects"][key]["hasTrustDialogAccepted"] is True
