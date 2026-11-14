#!/usr/bin/env python3
"""0.58.3 (user 2026-11-14): the local model-catalog overlay lives in ONE per-user store,
~/.claude/free-agents/model-catalog.local.psv, read the same way by every copy of free-agents
(git checkout, plugin cache, Homebrew), and migrated there from every install layout by the
normal update routes (SessionStart hook, csl startup) — no machine gets special treatment.

Every layout below is synthetic under a tmp HOME; none touches this machine's files.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bin" / "la-catalog-overlay.sh"
NAME = "model-catalog.local.psv"
sys.path.insert(0, str(ROOT / "bin"))


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def env_for(home, **extra):
    e = {k: v for k, v in os.environ.items()
         if k not in ("LA_MODEL_CATALOG_LOCAL", "LA_OVERLAY_SCAN_ROOTS", "CLAUDE_PLUGINS_DIR")}
    e["HOME"] = str(home)
    e.update(extra)
    return e


def store(home):
    return home / ".claude" / "free-agents" / NAME


def sh(home, *args, **extra):
    return subprocess.run([os.environ.get("LA_TEST_BASH", "bash"), str(HELPER), *args], env=env_for(home, **extra),
                          capture_output=True, text=True, timeout=30)


def py_path(home, config_dir, **extra):
    """overlay_path() evaluated in a child so HOME/env are the test's, not ours."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import catalog_overlay as c\n"
            "from pathlib import Path\n"
            "try:\n    p = c.overlay_path(Path(sys.argv[2])); print(p or '', end='')\n"
            "except c.OverlayOverrideError as e:\n    print(e, file=sys.stderr); sys.exit(2)\n")
    return subprocess.run([sys.executable, "-c", code, str(ROOT / "bin"), str(config_dir)],
                          env=env_for(home, **extra), capture_output=True, text=True, timeout=30)


# ---- Task 1: the resolver --------------------------------------------------------------------

def _state(home, tmp_path, which):
    cfg = tmp_path / "copy" / "config"
    cfg.mkdir(parents=True)
    extra = {}
    if which in ("store", "both"):
        store(home).parent.mkdir(parents=True)
        store(home).write_text("s|S|r|main|s\n")
    if which in ("legacy", "both"):
        (cfg / NAME).write_text("l|L|r|main|l\n")
    if which == "override":
        o = tmp_path / "elsewhere.psv"
        o.write_text("o|O|r|main|o\n")
        extra["LA_MODEL_CATALOG_LOCAL"] = str(o)
    expected = {"none": "", "store": str(store(home)), "both": str(store(home)),
                "legacy": str(cfg / NAME), "override": extra.get("LA_MODEL_CATALOG_LOCAL", "")}[which]
    return cfg, extra, expected


@pytest.mark.parametrize("which", ["none", "store", "legacy", "both", "override"])
def test_shell_and_python_agree(home, tmp_path, which):
    cfg, extra, expected = _state(home, tmp_path, which)
    s = sh(home, "path", "--config-dir", str(cfg), **extra)
    p = py_path(home, cfg, **extra)
    assert s.returncode == 0 and p.returncode == 0, s.stderr + p.stderr
    assert s.stdout == expected
    assert p.stdout == expected


def test_none_anywhere_returns_nothing(home, tmp_path):
    cfg, _, _ = _state(home, tmp_path, "none")
    r = sh(home, "path", "--config-dir", str(cfg))
    # exit 0 too: an empty stdout alone also passes when the helper is MISSING (vacuous green)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == "", r.stderr


def test_store_wins_over_legacy(home, tmp_path):
    cfg, _, _ = _state(home, tmp_path, "both")
    assert sh(home, "path", "--config-dir", str(cfg)).stdout == str(store(home))


def test_override_missing_is_loud(home, tmp_path):
    cfg, _, _ = _state(home, tmp_path, "store")
    bad = str(tmp_path / "nope.psv")
    s = sh(home, "path", "--config-dir", str(cfg), LA_MODEL_CATALOG_LOCAL=bad)
    assert s.returncode == 2 and s.stdout == ""
    assert "LA_MODEL_CATALOG_LOCAL" in s.stderr and bad in s.stderr
    p = py_path(home, cfg, LA_MODEL_CATALOG_LOCAL=bad)
    assert p.returncode == 2 and "LA_MODEL_CATALOG_LOCAL" in p.stderr


def test_sourced_function_matches_cli(home, tmp_path):
    cfg, _, expected = _state(home, tmp_path, "legacy")
    r = subprocess.run([os.environ.get("LA_TEST_BASH", "bash"), "-c", f'. "{HELPER}"; la_catalog_overlay_path "{cfg}"'],
                       env=env_for(home), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout == expected, r.stderr


def test_help_and_bad_flag(home):
    h = sh(home, "--help")
    assert h.returncode == 0 and "path" in h.stdout and "migrate" in h.stdout
    b = sh(home, "--bogus")
    assert b.returncode == 2 and "usage" in b.stderr.lower()


# ---- Task 2: migration from every layout ------------------------------------------------------

ORIG = "mine|My Model — note|org/repo|main|my-dir\n"


def layout(home, tmp_path):
    """A fresh 0.58.3 copy plus the three places an existing overlay can sit."""
    new_cfg = tmp_path / "new-copy" / "config"
    new_cfg.mkdir(parents=True)
    cache_old = home / ".claude" / "plugins" / "cache" / "mkt" / "free-agents" / "0.57.0" / "config"
    cellar_old = tmp_path / "brew" / "Cellar" / "free-agents" / "0.57.2" / "libexec" / "config"
    for d in (cache_old, cellar_old):
        d.mkdir(parents=True)
    roots = ":".join(str(d) for d in (new_cfg, cache_old, cellar_old))
    return new_cfg, cache_old, cellar_old, roots


def migrate(home, cfg, roots, *more, **extra):
    return sh(home, "migrate", "--config-dir", str(cfg), *more,
              LA_OVERLAY_SCAN_ROOTS=roots, **extra)


def assert_migrated_from(home, src):
    assert store(home).read_text() == ORIG
    assert src.is_symlink() and src.resolve() == store(home).resolve()


def test_checkout_user_moves_and_symlinks(home, tmp_path):
    cfg, _, _, roots = layout(home, tmp_path)
    (cfg / NAME).write_text(ORIG)
    r = migrate(home, cfg, roots)
    assert r.returncode == 0, r.stderr
    assert_migrated_from(home, cfg / NAME)
    assert len(r.stderr.strip().splitlines()) == 1 and "moved" in r.stderr


def test_plugin_user_overlay_in_old_cache_version_is_migrated(home, tmp_path):
    cfg, cache_old, _, roots = layout(home, tmp_path)
    (cache_old / NAME).write_text(ORIG)
    r = migrate(home, cfg, roots)
    assert r.returncode == 0, r.stderr
    assert_migrated_from(home, cache_old / NAME)


def test_plugin_cache_is_scanned_by_default(home, tmp_path):
    """Without LA_OVERLAY_SCAN_ROOTS, the default globs find an old cache version under HOME.
    The brew prefixes are pinned to tmp: a default scan must never reach this machine's
    real /opt/homebrew (it could MOVE a real user file)."""
    cfg, cache_old, _, _ = layout(home, tmp_path)
    (cache_old / NAME).write_text(ORIG)
    r = sh(home, "migrate", "--config-dir", str(cfg),
           LA_OVERLAY_BREW_PREFIXES=str(tmp_path / "no-brew"))
    assert r.returncode == 0, r.stderr
    assert_migrated_from(home, cache_old / NAME)


def test_brew_cellar_is_scanned_by_default(home, tmp_path):
    cfg, _, cellar_old, _ = layout(home, tmp_path)
    (cellar_old / NAME).write_text(ORIG)
    r = sh(home, "migrate", "--config-dir", str(cfg), LA_OVERLAY_BREW_PREFIXES=str(tmp_path / "brew"))
    assert r.returncode == 0, r.stderr
    assert_migrated_from(home, cellar_old / NAME)


def test_brew_user_overlay_in_old_cellar_version_is_migrated(home, tmp_path):
    cfg, _, cellar_old, roots = layout(home, tmp_path)
    (cellar_old / NAME).write_text(ORIG)
    r = migrate(home, cfg, roots)
    assert r.returncode == 0, r.stderr
    assert_migrated_from(home, cellar_old / NAME)


def test_identical_copies_everywhere_one_store_rest_symlinks(home, tmp_path):
    cfg, cache_old, cellar_old, roots = layout(home, tmp_path)
    for d in (cfg, cache_old, cellar_old):
        (d / NAME).write_text(ORIG)
    r = migrate(home, cfg, roots)
    assert r.returncode == 0, r.stderr
    for d in (cfg, cache_old, cellar_old):
        assert_migrated_from(home, d / NAME)


def test_different_copies_warn_and_change_nothing(home, tmp_path):
    cfg, cache_old, _, roots = layout(home, tmp_path)
    (cfg / NAME).write_text(ORIG)
    (cache_old / NAME).write_text("other|Other|r|main|o\n")
    r = migrate(home, cfg, roots)
    assert r.returncode == 0
    assert not store(home).exists()
    assert (cfg / NAME).read_text() == ORIG and not (cfg / NAME).is_symlink()
    assert (cache_old / NAME).read_text().startswith("other")
    for p in (cfg / NAME, cache_old / NAME, store(home)):
        assert str(p) in r.stderr


def test_store_exists_and_differs_from_a_legacy_copy_warns(home, tmp_path):
    cfg, _, _, roots = layout(home, tmp_path)
    store(home).parent.mkdir(parents=True)
    store(home).write_text("store|S|r|main|s\n")
    (cfg / NAME).write_text(ORIG)
    r = migrate(home, cfg, roots)
    assert r.returncode == 0
    assert store(home).read_text().startswith("store")
    assert (cfg / NAME).read_text() == ORIG and not (cfg / NAME).is_symlink()
    assert str(cfg / NAME) in r.stderr and str(store(home)) in r.stderr


def test_second_run_is_silent(home, tmp_path):
    cfg, _, _, roots = layout(home, tmp_path)
    (cfg / NAME).write_text(ORIG)
    migrate(home, cfg, roots)
    r = migrate(home, cfg, roots)
    assert r.returncode == 0 and r.stderr == "" and r.stdout == ""
    assert_migrated_from(home, cfg / NAME)


def test_nothing_anywhere_is_silent(home, tmp_path):
    cfg, _, _, roots = layout(home, tmp_path)
    r = migrate(home, cfg, roots)
    assert r.returncode == 0 and r.stderr == "" and r.stdout == ""
    assert not store(home).exists()


def test_dry_run_changes_nothing(home, tmp_path):
    cfg, _, _, roots = layout(home, tmp_path)
    (cfg / NAME).write_text(ORIG)
    r = migrate(home, cfg, roots, "--dry-run")
    assert r.returncode == 0 and str(cfg / NAME) in r.stderr
    assert not store(home).exists() and not (cfg / NAME).is_symlink()


def test_hook_mode_prints_on_stdout_and_exits_0_even_on_conflict(home, tmp_path):
    cfg, cache_old, _, roots = layout(home, tmp_path)
    (cfg / NAME).write_text(ORIG)
    (cache_old / NAME).write_text("other|Other|r|main|o\n")
    r = migrate(home, cfg, roots, "--hook")
    assert r.returncode == 0 and str(store(home)) in r.stdout and r.stderr == ""


def test_hooks_json_registers_the_migration_on_session_start():
    d = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    cmds = [h.get("command", "") for e in d["hooks"].get("SessionStart", []) for h in e["hooks"]]
    assert any('la-catalog-overlay.sh" migrate --hook' in c for c in cmds), cmds


def test_csl_runs_the_migration_before_dispatch():
    src = (ROOT / "bin" / "csl").read_text()
    i = src.find("la-catalog-overlay.sh\" migrate")
    assert i != -1, "bin/csl never runs the overlay migration"
    first_exec = src.find("exec ")
    assert first_exec == -1 or i < first_exec, "the migration must run before csl execs anything"


# ---- Task 3: every reader goes through the resolver -------------------------------------------

def test_no_reader_hardcodes_the_overlay_path():
    """The choke point: only the two helpers may name the overlay file. A reader that builds
    <copy>/config/model-catalog.local.psv itself is blind to the per-user store again."""
    allowed = {ROOT / "bin" / "la-catalog-overlay.sh", ROOT / "bin" / "catalog_overlay.py"}
    scanned, offenders = 0, []
    for base in ("bin", "install", "config"):
        for p in (ROOT / base).rglob("*"):
            if not p.is_file() or p.suffix not in (".sh", ".py", ".zsh", "") or "__pycache__" in p.parts:
                continue
            try:
                text = p.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            scanned += 1
            if NAME in text and p not in allowed:
                offenders.append(str(p.relative_to(ROOT)))
    assert scanned > 50, f"scanned only {scanned} files — the scan itself is broken"
    assert offenders == [], offenders


def test_download_models_lists_store_overlay_once(home, tmp_path):
    store(home).parent.mkdir(parents=True)
    store(home).write_text("zz-store-only|ZZ Store Model — x|org/zz|main|zz-store-only-dir\n")
    r = subprocess.run(["bash", str(ROOT / "install" / "download-models.sh"), "--list"],
                       env=env_for(home), capture_output=True, text=True, timeout=60)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2000:]
    assert "zz-store-only" in out
    assert out.count(f"Catalog: {store(home)} ") == 1, out[-2000:]


def test_model_profile_reads_store_overlay(home):
    store(home).parent.mkdir(parents=True)
    store(home).write_text("zz|ZZ|org/zz|main|zz\n")
    code = ("import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('mp', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
            "sys.path.insert(0, sys.argv[2]); spec.loader.exec_module(m)\n"
            "print('\\n'.join(str(c) for c in m.CATALOG_FILES))\n")
    r = subprocess.run([sys.executable, "-c", code, str(ROOT / "bin" / "la-model-profile.py"), str(ROOT / "bin")],
                       env=env_for(home), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert str(store(home)) in r.stdout.splitlines(), r.stdout
