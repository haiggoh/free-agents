#!/usr/bin/env python3
"""probe-param-coverage.py — how many of a provider's catalog ids have a parameter count?

0.54.0 Task 3: run against the REAL catalog (read-only GETs only, no generation), with the
HF cache either read-only (offline) or warmed on a TEMP COPY (--online-temp-cache never
touches the real ~/.cache/free-agents/hf-params.json).

Usage:
  probe-param-coverage.py --alias nvidia-minimax-m3-1-flash                 # offline: real cache, read-only
  probe-param-coverage.py --alias nvidia-minimax-m3-1-flash --online-temp-cache   # warm a temp copy online (<=170 HF GETs)
  probe-param-coverage.py --help

Exit status is NON-ZERO when the catalog listing returns no ids — an empty listing is a
broken probe, never "all sized". `--online-temp-cache` copies the real cache to a temp file,
points LA_HF_PARAMS_CACHE at the copy for both the warm-up and the count, and deletes the
copy at exit.

Env: LA_HF_PARAMS_CACHE (cache file; overridden by --online-temp-cache), LA_API_KEYS_DIR,
     LA_REMOTE_RATE_LIMIT=0 keeps the NVIDIA limiter out of the probe (catalog listing is
     read-only but still acquires when enabled).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="probe-param-coverage.py",
        description="Parameter-count coverage of a provider's catalog ids (read-only; no generation).",
        epilog="Env: LA_HF_PARAMS_CACHE, LA_API_KEYS_DIR, LA_REMOTE_RATE_LIMIT.")
    ap.add_argument("--alias", required=True,
                    help="roster alias whose provider catalog to probe (e.g. nvidia-minimax-m3-1-flash); "
                         "remote-session.sh --models needs a known ALIAS — a bare provider name exits 2")
    ap.add_argument("--online-temp-cache", action="store_true",
                    help="copy the real HF cache to a temp file, warm THAT copy online, and delete "
                         "it at exit; the real cache is never written")
    args = ap.parse_args(argv)

    os.environ.setdefault("LA_REMOTE_RATE_LIMIT", "0")

    tmp_copy = None
    if args.online_temp_cache:
        real = os.environ.get("LA_HF_PARAMS_CACHE") or str(
            Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "free-agents/hf-params.json")
        tmp = tempfile.mkdtemp(prefix="fa-probe-hf-")
        tmp_copy = Path(tmp) / "hf-params.json"
        if os.path.exists(real):
            shutil.copy(real, tmp_copy)
        else:
            tmp_copy.write_text("{}")
        os.environ["LA_HF_PARAMS_CACHE"] = str(tmp_copy)

    try:
        import session_picker as sp
        r = subprocess.run(["bash", str(REPO / "bin/remote-session.sh"), "--include-trials",
                            "--models", args.alias], capture_output=True, text=True, timeout=180)
        ids = [ln.split("\t")[0] for ln in r.stdout.splitlines() if "\t" in ln]
        if r.returncode != 0 or not ids:
            print(f"probe broken: rc={r.returncode} ids={len(ids)} stderr={r.stderr.strip()[-200:]}",
                  file=sys.stderr)
            return 2
        if args.online_temp_cache:
            # warm the temp copy online (bounded), then count
            sp._warm_hf_cache(ids)
        unsized = [mid for mid in ids if not sp.model_param_count("nvidia", mid)]
        print(f"catalog ids: {len(ids)}  sized: {len(ids) - len(unsized)}  unsized: {len(unsized)}")
        for mid in unsized:
            print("  NO SIZE:", mid)
        return 0
    finally:
        if tmp_copy is not None:
            shutil.rmtree(tmp_copy.parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
