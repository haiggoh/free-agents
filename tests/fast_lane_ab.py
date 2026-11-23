#!/usr/bin/env python3
"""A/B harness for the 0.63.0 fast lane (waypoint fast-lane-for-agents-offloads, spec §3).

Runs a FIXED dispatch set twice through bin/free-agent-tool.py --out envelopes — once with
LA_LIMITER_FAST_LANE=off (0.62.3 control arm) and once =agents — and prints the metrics table:
attempt timeout/stall rate, mean grant wait, and per-arm wall time.

Usage: python3 tests/fast_lane_ab.py [--dispatches N] [--prompt TEXT] [--out DIR]
Env:   LA_LIMITER_FAST_LANE is set per arm by the harness; everything else inherited.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "bin" / "free-agent-tool.py"


def run_arm(arm: str, n: int, prompt: str, outdir: Path) -> dict:
    env = dict(os.environ, LA_LIMITER_FAST_LANE=arm)
    t0 = time.time()
    timeouts = 0
    attempts = 0
    for i in range(n):
        r = subprocess.run([sys.executable, str(TOOL), "--prompt", prompt,
                            "--out", str(outdir / f"{arm}-{i}.json")],
                           env=env, capture_output=True, text=True, timeout=1200)
        env_file = outdir / f"{arm}-{i}.json"
        if env_file.exists():
            try:
                doc = json.loads(env_file.read_text())
            except ValueError:
                doc = {}
            for att in doc.get("attempts", []):
                attempts += 1
                err = str(att.get("error", ""))
                if att.get("ok") is False and ("timeout" in err or "stall" in err):
                    timeouts += 1
        if r.returncode != 0:
            print(f"  [{arm} {i}] rc={r.returncode}: {(r.stderr or '')[-160:]}", file=sys.stderr)
    wall = time.time() - t0
    return {"arm": arm, "dispatches": n, "attempts": attempts, "timeouts": timeouts,
            "timeout_rate": (timeouts / attempts) if attempts else None,
            "wall_s": round(wall, 1),
            "mean_s_per_dispatch": round(wall / n, 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dispatches", type=int, default=10)
    ap.add_argument("--prompt", default="Reply with exactly: ok")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    outdir = Path(args.out or tempfile.mkdtemp(prefix="fastlane-ab-"))
    outdir.mkdir(parents=True, exist_ok=True)
    rows = [run_arm("off", args.dispatches, args.prompt, outdir),
            run_arm("agents", args.dispatches, args.prompt, outdir)]
    print(f"\nfast-lane A/B ({args.dispatches} dispatches/arm, envelopes in {outdir})")
    print(f"{'arm':<8} {'attempts':>8} {'timeouts':>8} {'timeout_rate':>12} {'wall_s':>8} {'s/dispatch':>10}")
    for r in rows:
        rate = f"{r['timeout_rate']:.2%}" if r["timeout_rate"] is not None else "-"
        print(f"{r['arm']:<8} {r['attempts']:>8} {r['timeouts']:>8} {rate:>12} "
              f"{r['wall_s']:>8} {r['mean_s_per_dispatch']:>10}")
    off, on = rows
    if off["timeout_rate"] is not None and on["timeout_rate"] is not None:
        delta = on["timeout_rate"] - off["timeout_rate"]
        print(f"\ntimeout-rate delta (agents - off): {delta:+.2%}  "
              f"({'fast lane helps' if delta < 0 else 'no help / regression' if delta > 0 else 'no change'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
