#!/usr/bin/env python3
"""tests/test_memguard_window.py — Phase 4 plumbing: D6 launch-time window + KV flags.

What this protects: the memory guard's plumbing-only phase. `la-memguard-window.py`
derives a RAM-calibrated auto-compact window from seeded per-(backend, KV-dtype)
bytes-per-token figures (Phase 6 measures the real ones), clamps it to the
Claude Code autoCompactWindow floor, never exceeds the model's context, and
maps a per-model KV precision onto each backend's serve flag — refusing
rapid-mlx architectures that reject explicit int8/int4 BEFORE a server can
fail to start. Each test names the production change that would make it fail.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

script_path = Path(__file__).resolve().parents[1] / "bin" / "la-memguard-window.py"
spec = importlib.util.spec_from_file_location("la_memguard_window", script_path)
assert spec is not None and spec.loader is not None
window = importlib.util.module_from_spec(spec)
spec.loader.exec_module(window)


# --- architecture detection ----------------------------------------------------

def test_family_detects_rapid_rejected_archs():
    # Fails if the sliding-window / MLA detection stops matching these repo ids.
    assert window.family("mlx-community/gemma-3-12b-it-4bit") == "sliding-window"
    assert window.family("openai/gpt-oss-20b") == "sliding-window"
    assert window.family("mlx-community/DeepSeek-V3.1-MLX") == "mla"
    assert window.family("moonshotai/Kimi-K2.5-MLX") == "mla"
    assert window.family("mlx-community/Qwen3.6-35B-A3B-TurboQuant-MLX-4bit") is None


def test_rapid_kv_auto_always_allowed():
    # Fails if `auto` (the default everywhere) is ever refused.
    ok, reason = window.rapid_kv_allowed("mlx-community/gemma-3-12b-it-4bit", "auto")
    assert ok and reason is None


def test_rapid_int8_refused_for_rejected_archs():
    # Fails if a rejected architecture can reach a rapid serve command with explicit int8.
    for model_id in ("mlx-community/gemma-3-12b-it-4bit", "openai/gpt-oss-20b",
                     "mlx-community/DeepSeek-V3.1-MLX", "moonshotai/Kimi-K2.5-MLX"):
        ok, reason = window.rapid_kv_allowed(model_id, "int8")
        assert not ok, model_id
        assert reason
    for model_id in ("mlx-community/gemma-3-12b-it-4bit", "openai/gpt-oss-20b",
                     "mlx-community/DeepSeek-V3.1-MLX", "moonshotai/Kimi-K2.5-MLX"):
        ok, _ = window.rapid_kv_allowed(model_id, "int4")
        assert not ok, model_id


def test_rapid_int8_allowed_for_qwen():
    # Fails if a KV-friendly architecture is wrongly refused.
    ok, reason = window.rapid_kv_allowed("mlx-community/Qwen3.6-35B-A3B-TurboQuant-MLX-4bit", "int8")
    assert ok and reason is None


# --- KV flag mapping -----------------------------------------------------------

def test_kv_flags_rapid_and_llama():
    # Fails if the backend flag spelling drifts (rapid --kv-cache-dtype; llama.cpp -ctk/-ctv).
    assert window.kv_flags("rapid", "qwen", "int8") == ("--kv-cache-dtype", "int8")
    assert window.kv_flags("llama", "qwen", "int8") == ("-ctk", "q8_0", "-ctv", "q8_0")
    assert window.kv_flags("llama", "qwen", "int4") == ("-ctk", "q4_0", "-ctv", "q4_0")


def test_kv_flags_auto_emits_nothing():
    # Fails if `auto` adds a flag instead of leaving the backend default.
    for backend in ("rapid", "llama", "omlx", "vllm"):
        assert window.kv_flags(backend, "qwen", "auto") == ()


def test_kv_flags_vllm_has_none():
    # Fails if a KV flag is invented for vllm-mlx, which has none (plan fact 6).
    assert window.kv_flags("vllm", "qwen", "int8") == ()


# --- window math ---------------------------------------------------------------

def test_window_clamps_to_floor_for_qwen_seed():
    # Fails if the window can be set below the Claude Code floor (U2): the seeded
    # Qwen3.8-27B-on-rapid figure yields ~95.6k tokens, which must clamp to 100k.
    got = window.window_tokens(weights_gb=20.4, gb_per_1k=0.7, ceiling_gb=103.9,
                               soft_pct=85, context_tokens=128000)
    assert got == 100000


def test_window_math_unclamped():
    # Fails if the (ceiling*soft% - weights - overhead)/rate arithmetic drifts.
    got = window.window_tokens(weights_gb=10.0, gb_per_1k=0.35, ceiling_gb=103.9,
                               soft_pct=85, context_tokens=None)
    assert got == 220900


def test_window_never_exceeds_context():
    # Fails if window_tokens ignores the model context cap (plan: min(that, context)).
    got = window.window_tokens(weights_gb=10.0, gb_per_1k=0.35, ceiling_gb=103.9,
                               soft_pct=85, context_tokens=200000)
    assert got == 200000


def test_window_never_exceeds_validator_ceiling():
    # Fails if the window can exceed Claude Code's own 1M-token autocompact validator ceiling —
    # the launcher would then refuse its own computed value at launch time.
    got = window.window_tokens(weights_gb=1.0, gb_per_1k=0.01, ceiling_gb=103.9,
                               soft_pct=85, context_tokens=None)
    assert got == 1_000_000


def test_window_oversized_weights_stay_at_floor():
    # Fails if a negative RAM budget produces a zero/negative window instead of the floor.
    got = window.window_tokens(weights_gb=90.0, gb_per_1k=0.7, ceiling_gb=103.9,
                               soft_pct=85, context_tokens=128000)
    assert got == 100000


def test_window_sub_floor_context_returns_floor():
    # Fails if the FINAL floor clamp is removed: a context smaller than the floor would
    # shrink the request below what Claude Code silently enforces (plan U2) — for those
    # models D1–D3, not the window, are the guard.
    got = window.window_tokens(weights_gb=10.0, gb_per_1k=0.35, ceiling_gb=103.9,
                               soft_pct=85, context_tokens=60000)
    assert got == 100000


def test_rate_int8_halves_seeded_bf16():
    # Fails if the seeded int8 figure stops matching fact 6 (KV halves at int8).
    assert window.resolve_rate("rapid", "int8", "qwen") * 2 == window.resolve_rate("rapid", "auto", "qwen")


# --- backend state listing ------------------------------------------------------

def test_backend_status_lists_every_backend():
    # Fails if a backend's KV plumbing state is silently missing (adversarial review:
    # "Only oMLX wired → other backends silently unprotected → Phase 4 lists each backend's state").
    status = window.backend_status()
    assert set(status) == {"rapid", "llama", "omlx", "vllm", "mlx_lm", "cuda-vllm"}
    assert status["rapid"] == "flag"
    assert status["llama"] == "flag"
    assert status["omlx"] == "not-plumbed-live-config"
    assert status["vllm"] == "no-flag"
    assert status["mlx_lm"] == "no-flag"
    assert status["cuda-vllm"] == "not-plumbed"


def test_model_rates_take_precedence_and_key_on_backend():
    # Fails if the Phase 6 per-model table cannot override the (backend, kv) seed, or if it
    # cannot distinguish the same model id on two backends (workflow finding: the plan's
    # figures are per (model, BACKEND, KV dtype) — the key must carry the backend).
    window.MODEL_RATES.clear()
    window.MODEL_RATES[("mlx-community/Qwen3.0-1.7B-4bit", "rapid", "int8")] = 0.1
    assert window.resolve_rate("rapid", "int8", "mlx-community/Qwen3.0-1.7B-4bit") == 0.1
    assert window.resolve_rate("llama", "int8", "mlx-community/Qwen3.0-1.7B-4bit") == 0.35
    window.MODEL_RATES.clear()


# --- CLI ------------------------------------------------------------------------

def _cli(*args):
    return subprocess.run([sys.executable, str(script_path), *args],
                          capture_output=True, text=True)


def test_cli_window_prints_int():
    # Fails if the launcher-facing CLI output is not a bare integer.
    r = _cli("window", "--weights-gb", "20.4", "--backend", "rapid", "--kv", "auto",
             "--context-tokens", "128000")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "100000"


def test_cli_window_below_floor_prints_nothing():
    # Fails if a sub-floor-context model gets a window exported anyway: no valid window exists
    # inside Claude Code's accepted range, and its native overflow compaction is flag-gated —
    # an oversized window would let the model crash on context before compaction ever fires.
    # The watchdog (D1–D3), not the window, is that model's guard. (Review finding, Gemini #3.)
    r = _cli("window", "--weights-gb", "10.0", "--backend", "rapid", "--kv", "auto",
             "--context-tokens", "32768")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_cli_kv_flags_print_flags():
    r = _cli("kv-flags", "--backend", "rapid", "--model-id", "mlx-community/Qwen3.6-4bit", "--kv", "int8")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "--kv-cache-dtype int8"


def test_cli_kv_flags_refusal_exits_nonzero_with_reason():
    # Fails if a rejected arch can pass the CLI without a non-zero exit + stderr reason.
    r = _cli("kv-flags", "--backend", "rapid", "--model-id", "mlx-community/gemma-3-12b-it-4bit", "--kv", "int8")
    assert r.returncode != 0
    assert "sliding-window" in r.stderr


def test_cli_help_exits_zero():
    # Fails if --help is missing (every script supports --help) or performs real work.
    r = _cli("--help")
    assert r.returncode == 0
    assert "usage" in r.stdout.lower()
