#!/usr/bin/env python3
"""la-memguard-window.py — Phase 4 plumbing: D6 launch-time auto-compact window + KV flags.

What this is: the RAM-calibrated half of the memory guard, wired into the launchers
BEHIND A DEFAULT-OFF SWITCH (`LA_MEMGUARD_WINDOW`, see launch-claude-agent.sh). The
window math is seeded, not measured: Phase 6 records the real per-model figures and
the D8 trade-off text, and until then the switch stays off. Two jobs:

  window      tokens at which weights+overhead+KV reach (ceiling * soft%) — the
              auto-compact window Claude Code should use; clamped to the
              autoCompactWindow floor (U2, assumed 100k until measured) and
              capped by the model's context.
  kv-flags    map a per-model KV precision (session_menu_state.py kv-get) onto a
              backend's serve flag — rapid `--kv-cache-dtype`, llama.cpp
              `-ctk/-ctv` — and REFUSE rapid-mlx architectures that reject
              explicit int8/int4 (sliding-window: Gemma 3/4, GPT-OSS; MLA:
              DeepSeek V3+, Kimi K2.5) before a server can fail to start.

Backend KV state (adversarial review: Phase 4 lists each backend's state — see
`backend-status`): rapid and llama.cpp take flags; oMLX's turboquant lives in the
LIVE ~/.omlx/settings.json (user-gated, NOT plumbed here); vllm-mlx has no KV
flag at all (plan fact 6, revalidated 2026-10-18) so it is watchdog-only.

No environment variables are read: callers (bash launchers) map their env onto
the CLI flags below. Standard library only.
"""
from __future__ import annotations

import argparse
import sys

# Defaults mirror the statusline's RAM reader (bin/la-statusline-segment.sh): wired pages
# against a Metal cap, 103.9 GB on the 128 GB M4 Max. Same derivation as la-ram-preflight.sh.
DEFAULT_CEILING_GB = 103.9
DEFAULT_OVERHEAD_GB = 1.0
# Claude Code's plugin-agent validator bounds autoCompactWindow to 100k..1M; U2's live
# measurement is still open, so the floor is a named constant, never a magic number.
DEFAULT_FLOOR_TOKENS = 100_000
# Claude Code's autocompact validator accepts 100k–1m tokens (launch-claude-agent.sh enforces the
# same range), so the window never proposes more — the launcher would refuse its own value.
MAX_AUTOCOMPACT_TOKENS = 1_000_000
KV_CHOICES = ("auto", "int8", "int4")

# GB per 1k tokens of KV/context growth, seeded per (backend, kv dtype). Seeds come from the
# plan's measurements (Qwen3.8 27B 4-bit on rapid ≈ 0.6–0.8 GB/1k at bf16; int8 halves, int4
# quarters — plan fact 6). Phase 6 replaces these with per-model measured figures.
SEEDED_GB_PER_1K: dict[tuple[str, str], float] = {
    ("rapid", "auto"): 0.7,
    ("rapid", "int8"): 0.35,
    ("rapid", "int4"): 0.175,
    ("llama", "auto"): 0.7,
    ("llama", "int8"): 0.35,
    ("llama", "int4"): 0.175,
    ("omlx", "auto"): 0.7,
    ("vllm", "auto"): 0.7,
}
DEFAULT_GB_PER_1K = 0.7

# Phase 6 records measured per-model figures here; they take precedence over the seeds.
MODEL_RATES: dict[tuple[str, str], float] = {}

# rapid-mlx rejects explicit int8/int4 for sliding-window and MLA architectures (its own
# --help, plan fact 6): KV quantization is meaningless there and the server refuses to start.
RAPID_KV_REJECTED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sliding-window", ("gemma-3", "gemma-4", "gpt-oss")),
    ("mla", ("deepseek-v3", "deepseek-v4", "kimi-k2")),
)

BACKENDS = ("rapid", "llama", "omlx", "vllm")


class KvRefused(Exception):
    """A backend refuses this KV precision for this model (pre-launch refusal)."""


def family(model_id: str) -> str | None:
    """Architecture family rapid-mlx rejects for explicit KV quantization, if any."""
    mid = (model_id or "").lower()
    for fam, keys in RAPID_KV_REJECTED:
        for key in keys:
            if key in mid:
                return fam
    return None


def rapid_kv_allowed(model_id: str, kv: str) -> tuple[bool, str | None]:
    """(ok, reason). `auto` is always allowed: it asks for the backend default."""
    if kv == "auto":
        return True, None
    fam = family(model_id)
    if fam:
        return False, f"rapid-mlx rejects explicit {kv} for {fam} architectures: {model_id}"
    return True, None


def kv_flags(backend: str, model_id: str, kv: str) -> tuple[str, ...]:
    """Backend serve flags for this KV precision; empty for `auto` and flag-less backends.

    Raises KvRefused when rapid-mlx would reject the explicit dtype — the caller must
    refuse the launch rather than let the server die at startup.
    """
    if kv not in KV_CHOICES:
        raise ValueError(f"kv must be one of {KV_CHOICES}, not {kv!r}")
    if backend == "rapid":
        ok, reason = rapid_kv_allowed(model_id, kv)
        if not ok:
            raise KvRefused(reason or "rapid-mlx refuses this KV dtype")
        return ("--kv-cache-dtype", kv) if kv != "auto" else ()
    if backend == "llama":
        return {
            "int8": ("-ctk", "q8_0", "-ctv", "q8_0"),
            "int4": ("-ctk", "q4_0", "-ctv", "q4_0"),
        }.get(kv, ())
    # oMLX: turboquant lives in the live ~/.omlx/settings.json (user-gated — not plumbed).
    # vllm-mlx: no KV-dtype flag exists (plan fact 6). Both stay watchdog-only (D1).
    return ()


def window_tokens(weights_gb: float, gb_per_1k: float, ceiling_gb: float = DEFAULT_CEILING_GB,
                  soft_pct: float = 85, context_tokens: int | None = None,
                  floor_tokens: int = DEFAULT_FLOOR_TOKENS,
                  overhead_gb: float = DEFAULT_OVERHEAD_GB) -> int:
    """Token count at which weights + overhead + KV reach (ceiling * soft%).

    window = (ceiling*soft% - weights - overhead) / (GB per token), clamped to the
    autoCompactWindow floor (never propose a sub-floor value Claude Code would silently
    raise), then capped by the model context, then clamped to the floor again: a context
    smaller than the floor cannot shrink the request below what Claude Code enforces
    anyway — for those models D1–D3, not the window, are the guard (plan U2).
    """
    budget_gb = ceiling_gb * soft_pct / 100.0 - weights_gb - overhead_gb
    raw = int(budget_gb / (gb_per_1k / 1000.0)) if gb_per_1k > 0 else 0
    result = max(raw, floor_tokens)
    if context_tokens is not None:
        result = min(result, int(context_tokens))
    result = min(result, MAX_AUTOCOMPACT_TOKENS)
    return max(result, floor_tokens)


def resolve_rate(backend: str, kv: str, model_id: str) -> float:
    """Measured figure for (model, kv) if Phase 6 recorded one, else the (backend, kv) seed."""
    return MODEL_RATES.get((model_id, kv), SEEDED_GB_PER_1K.get((backend, kv), DEFAULT_GB_PER_1K))


def backend_status() -> dict[str, str]:
    """Each backend's KV plumbing state, listed so no backend is silently unprotected."""
    return {
        "rapid": "flag",
        "llama": "flag",
        "omlx": "not-plumbed-live-config",
        "vllm": "no-flag",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="la-memguard-window.py",
        description="Phase 4 memory-guard plumbing: RAM-calibrated auto-compact window and "
                    "per-backend KV serve flags (seeded figures; defaults unchanged until "
                    "Phase 6 measures).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("window", help="print the auto-compact window in tokens")
    w.add_argument("--weights-gb", type=float, required=True,
                   help="model weights footprint in GB (roster SIZE field)")
    w.add_argument("--backend", choices=BACKENDS, required=True)
    w.add_argument("--model-id", default="", help="repo/model id, for per-model figures later")
    w.add_argument("--kv", choices=KV_CHOICES, default="auto")
    w.add_argument("--ceiling-gb", type=float, default=DEFAULT_CEILING_GB,
                   help=f"machine RAM ceiling (default {DEFAULT_CEILING_GB})")
    w.add_argument("--soft-pct", type=float, default=85,
                   help="RAM percent the window targets (memguard soft_pct)")
    w.add_argument("--context-tokens", type=int, default=None,
                   help="model context; window never exceeds it. Below the floor, NOTHING is "
                        "printed: no valid window exists in Claude Code's range and the "
                        "watchdog (D1-D3) is that model's guard")
    w.add_argument("--floor-tokens", type=int, default=DEFAULT_FLOOR_TOKENS,
                   help=f"autoCompactWindow floor (default {DEFAULT_FLOOR_TOKENS}; U2 open)")
    w.add_argument("--overhead-gb", type=float, default=DEFAULT_OVERHEAD_GB,
                   help=f"non-KV runtime overhead in GB (default {DEFAULT_OVERHEAD_GB})")

    k = sub.add_parser("kv-flags", help="print the backend serve flags for a KV precision")
    k.add_argument("--backend", choices=BACKENDS, required=True)
    k.add_argument("--model-id", default="")
    k.add_argument("--kv", choices=KV_CHOICES, required=True)

    sub.add_parser("backend-status", help="list each backend's KV plumbing state")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "window":
        if args.context_tokens is not None and args.context_tokens < args.floor_tokens:
            # No valid window exists inside Claude Code's accepted range for this model, and its
            # native overflow compaction is flag-gated — an oversized window would let the model
            # hit the context wall before compaction ever fires. Print nothing: the launcher
            # skips an empty value, and the watchdog (D1–D3) is that model's guard.
            # (Plan U2; review finding Gemini #3.)
            return 0
        rate = resolve_rate(args.backend, args.kv, args.model_id)
        print(window_tokens(weights_gb=args.weights_gb, gb_per_1k=rate, ceiling_gb=args.ceiling_gb,
                            soft_pct=args.soft_pct, context_tokens=args.context_tokens,
                            floor_tokens=args.floor_tokens, overhead_gb=args.overhead_gb))
        return 0
    if args.cmd == "kv-flags":
        try:
            flags = kv_flags(args.backend, args.model_id, args.kv)
        except KvRefused as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 3
        print(" ".join(flags))
        return 0
    if args.cmd == "backend-status":
        for name, state in backend_status().items():
            print(f"{name} {state}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
