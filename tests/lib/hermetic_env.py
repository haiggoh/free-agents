#!/usr/bin/env python3
"""tests/lib/hermetic_env.py — the ONE way a python test builds a clean subprocess env.

0.56.7 (L2): mirrors tests/lib/hermetic_env.sh. Default passthrough is PATH, HOME and the
test-fence vars conftest forces (throttle state, HF params cache, quota ledger, notify
shim+sink), so an `env=` child never re-resolves the REAL machine-wide limiter state.
Empty fence vars are omitted, never passed empty. `extra` overrides/extends.

    env = hermetic_env({"HOME": str(sb), "PATH": f"{sb}/bin:/usr/bin:/bin"})
    subprocess.run([...], env=env)
"""
import os

FENCE_VARS = (
    "LA_NVIDIA_THROTTLE_STATE",
    "LA_HF_PARAMS_CACHE",
    "LA_QUOTA_LEDGER_DIR",
    "FA_NOTIFY_SINK",
    "FA_NOTIFY_SHIM_DIR",
)


def hermetic_env(extra: dict | None = None) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    for v in FENCE_VARS:
        val = os.environ.get(v)
        if val:                      # never pass empty: "" would re-resolve the real default
            env[v] = val
    if extra:
        env.update(extra)
    return env
