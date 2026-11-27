#!/usr/bin/env python3
"""Parallel-launch RMW race in bin/fa_pre_trust.py (the folder-trust writer).

Found 2026-11-27 while shipping 0.64.2: parallel remote-session.sh launches in the same
second all derived the SAME temp path (cfg + TMP_SUFFIX + strftime stamp, fa_pre_trust.py
stamp/tmp lines) — the backup path retries same-second collisions, the tmp path does not, so
a concurrent writer gets FileExistsError (exit 4). Separately, the whole
read -> modify -> os.replace runs without a lock, so concurrent writers can last-write-wins
and drop each other's project keys.

Both tests drive real subprocesses with a frozen clock. RED before the fix
(mkstemp tmp + flock around the RMW), GREEN after.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "bin" / "fa_pre_trust.py"
STAMP = "20261127T220000Z"

_WRITER = """
import importlib.util, os, sys, time
spec = importlib.util.spec_from_file_location("fapt", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.VERIFY_DELAY_S = 0.0
m.time.strftime = lambda *a, **k: os.environ.get("WRITER_STAMP", "20261127T220000Z")
mode = sys.argv[3]
if mode == "park_replace":
    flag, real = sys.argv[4], os.replace
    def slow(src, dst):
        with open(flag, "w") as f: f.write("x")
        time.sleep(2.0)
        real(src, dst)
    os.replace = slow
elif mode == "slow_load":
    real_load = m._load_config
    def slow_load(cfg):
        time.sleep(0.3)
        return real_load(cfg)
    m._load_config = slow_load
r = m.pre_trust(sys.argv[2], config_path=sys.argv[5])
sys.stdout.write(f"{r.exit_code} {r.reason}\\n")
"""


def _writer(tmp_path, target, mode, cfg, flag=None, stamp=STAMP):
    w = tmp_path / "writer.py"
    w.write_text(_WRITER)
    # snippet argv: 0 writer.py, 1 helper, 2 target, 3 mode, 4 flag, 5 config
    args = [sys.executable, str(w), str(HELPER), str(target), mode, str(flag or tmp_path / "entered")]
    args.append(str(cfg))
    env = dict(os.environ, WRITER_STAMP=stamp)
    return subprocess.Popen(args, stdout=subprocess.PIPE, text=True, env=env)


def _mkcfg(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"projects": {}}) + "\n")
    return cfg


def _mkdirs(tmp_path, names):
    out = []
    for n in names:
        d = tmp_path / n
        d.mkdir()
        out.append(d)
    return out


def _wait_flag(flag, timeout=15):
    for _ in range(int(timeout * 10)):
        if flag.exists():
            return True
        time.sleep(0.1)
    return False


def test_same_second_parallel_writers_both_succeed(tmp_path, monkeypatch):
    """Frozen clock: writer A parks holding its tmp at os.replace; writer B (same second)
    must still succeed. Pre-fix B derives the same tmp name -> O_EXCL FileExistsError, exit 4."""
    monkeypatch.setattr("time.strftime", lambda *a, **k: STAMP)
    cfg = _mkcfg(tmp_path)
    a, b = _mkdirs(tmp_path, ["a", "b"])
    flag = tmp_path / "entered"
    p = _writer(tmp_path, a, "park_replace", cfg, flag=flag)
    assert _wait_flag(flag), "writer A did not reach os.replace"
    # writer B in-process with the same frozen stamp: pre-fix it derives the same tmp name
    import importlib.util
    spec = importlib.util.spec_from_file_location("fapt_main", HELPER)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    setattr(m, "VERIFY_DELAY_S", 0.0)
    r_b = m.pre_trust(str(b), config_path=str(cfg))
    out = p.communicate(timeout=30)[0].strip()
    assert out.startswith("0"), f"writer A failed: {out}"
    assert r_b.exit_code == 0, f"same-second writer B failed pre-fix as expected, now: {r_b.reason}"


def test_concurrent_writers_keep_both_keys(tmp_path):
    """Two writers with a slow config load (both read the original before either writes)
    must both land their trust keys. Pre-fix there is no lock -> last os.replace wins and
    one key is lost."""
    cfg = _mkcfg(tmp_path)
    a, b = _mkdirs(tmp_path, ["a", "b"])
    # distinct stamps so the tmp-name collision cannot mask the lost update: pre-fix the
    # missing key is the unlocked RMW, not a shared tmp path
    pa = _writer(tmp_path, a, "slow_load", cfg, stamp=STAMP + "-1")
    pb = _writer(tmp_path, b, "slow_load", cfg, stamp=STAMP + "-2")
    outa = pa.communicate(timeout=60)[0].strip()
    outb = pb.communicate(timeout=60)[0].strip()
    assert outa.startswith("0") and outb.startswith("0"), f"{outa} | {outb}"
    projects = json.loads(cfg.read_text())["projects"]
    for d in (a, b):
        key = os.path.realpath(d)
        assert isinstance(projects.get(key), dict) and \
            projects[key].get("hasTrustDialogAccepted") is True, \
            f"lost update: {key} missing from final config (pre-fix symptom)"
