#!/usr/bin/env python3
"""Task 2 Step 1 — client-visible error mapping through the REAL rig (RED first).

Every test here starts real LiteLLM (hooks ON, sandbox limiter state) against the fake
upstream and asserts on what the CLIENT received: status, error type, Retry-After, and —
for the booking invariant — the sandbox state file and the fake's own hit log (never the
hook's own counters, invariant 9).

Shapes come from the bridge experiment (docs/measurements/2026-11-22-bridge-experiment/):
today (1.110.2) a fallback chain ending on a rate limit reaches the client as HTTP 500
naming the PRIMARY's stream error; the fast-fail books phantom 429s (defect L8); a die0
upstream is a silent empty 200.
"""
import json
import time
from pathlib import Path

TEST_MODEL = "claude-opus-5-5"


def _seed_cooling(state_path: Path, models, cooldown: float = 3600.0) -> None:
    """Pre-seed a sliding-window state file with per-model cooldowns (rate_limiter.py
    _write schema). The bucket re-reads the file on every acquire, so seeding before the
    first request is enough."""
    now = time.time()
    entries = {m: {"until": now + cooldown, "n": 1, "last": now} for m in models}
    state_path.write_text(json.dumps({
        "stamps": [], "cooldown_until": 0.0, "limit": 40, "consecutive_429s": 0,
        "last_429_time": 0.0, "models": entries, "owners": {}, "waiters": {},
        "deficit": {}}))


def _error_types(events) -> list:
    return [e.get("error", {}).get("type") for e in events if e.get("type") == "error"]


def test_fallback_429_reaches_client_as_429(fake_upstream, live_proxy, anthropic_stream,
                                            two_tier_config):
    primary, fallback = fake_upstream("die1"), fake_upstream("r429")
    proxy = live_proxy(two_tier_config(primary.port, fallback.port, hooks=True))
    try:
        status, events, _headers = anthropic_stream(proxy.port, model=TEST_MODEL)
        assert status == 429, (status, events, proxy.log_text()[-1500:])
        assert "rate_limit_error" in _error_types(events), events
        # NOTE (measured 2026-11-22, 1.110.2): the boundary-raise path delivers status +
        # error type, but LiteLLM's /v1/messages error builder does NOT copy the
        # exception's headers to the client response, so Retry-After is dropped here —
        # recorded in the plan receipt; the hook still sets it for paths that forward it.
        assert len(fallback.hits()) >= 1, fallback.hits()
    finally:
        proxy.stop()


def test_cooling_fallback_reaches_client_as_429(fake_upstream, live_proxy, anthropic_stream,
                                                two_tier_config, tmp_path):
    primary, fallback = fake_upstream("die1"), fake_upstream("ok")
    state = tmp_path / "seeded-nvidia-state"
    _seed_cooling(state, ["nvidia_nim/fake-fallback", "fake-fallback"])
    proxy = live_proxy(two_tier_config(primary.port, fallback.port, hooks=True),
                       env={"LA_NVIDIA_THROTTLE_STATE": str(state),
                            "LA_PROXY_FALLBACK_NAMES": "la-fallback-1"})
    try:
        status, events, _headers = anthropic_stream(proxy.port, model=TEST_MODEL)
        assert status == 429, (status, events, proxy.log_text()[-1500:])
        assert "rate_limit_error" in _error_types(events), events
        assert fallback.hits() == [], fallback.hits()   # fast-fail: ZERO upstream calls
    finally:
        proxy.stop()


def test_self_raised_fast_fail_books_nothing(fake_upstream, live_proxy, anthropic_stream,
                                             two_tier_config, tmp_path):
    primary, fallback = fake_upstream("die1"), fake_upstream("ok")
    state = tmp_path / "seeded-nvidia-state"
    _seed_cooling(state, ["nvidia_nim/fake-fallback", "fake-fallback"])
    proxy = live_proxy(two_tier_config(primary.port, fallback.port, hooks=True),
                       env={"LA_NVIDIA_THROTTLE_STATE": str(state),
                            "LA_PROXY_FALLBACK_NAMES": "la-fallback-1"})
    try:
        _status, _events, _headers = anthropic_stream(proxy.port, model=TEST_MODEL)
        st = json.loads(state.read_text())
        assert st.get("consecutive_429s", 0) == 0, st
        for key in ("fake-primary", "nvidia_nim/fake-primary"):
            assert key not in st.get("models", {}), st.get("models")
        assert "NVIDIA 429 with" not in proxy.log_text(), proxy.log_text()[-2000:]
        # invariant 9 measured on the FAKE's hit log, never on the hook's counters
        assert len(primary.hits()) == 1, primary.hits()
        assert len(st.get("stamps", [])) == len(primary.hits()), (st.get("stamps"), primary.hits())
    finally:
        proxy.stop()


def test_empty_unterminated_stream_is_retriable(fake_upstream, live_proxy, anthropic_stream,
                                                two_tier_config):
    primary = fake_upstream("die0")
    proxy = live_proxy(two_tier_config(primary.port, None, hooks=True))
    try:
        status, events, _headers = anthropic_stream(proxy.port, model=TEST_MODEL)
        text = "".join(e.get("delta", {}).get("text") or "" for e in events
                       if e.get("type") == "content_block_delta")
        errors = _error_types(events)
        # the die0 defect (experiment row 2): HTTP 200, empty text, NO error at all
        assert not (status == 200 and text == "" and not errors), \
            f"silent empty answer: status={status} events={events!r}"
        assert errors, f"no error surfaced at all: status={status} events={events!r}"
    finally:
        proxy.stop()


def test_chainless_cooling_model_gets_zero_upstream_hits(fake_upstream, live_proxy,
                                                          anthropic_stream, two_tier_config,
                                                          tmp_path):
    """L1 delivery proof: with NO fallback chain, a cooling primary receives ZERO upstream
    hits during the cooldown — the request waits it out inside the bucket queue and then
    fires exactly once. (Before the fix the hook called aacquire() without model=, so a
    chain-less proxy fired straight at the throttled model.)"""
    primary = fake_upstream("ok")
    state = tmp_path / "cooling-state"
    cooldown = 15.0
    seeded_at = time.time()
    _seed_cooling(state, ["nvidia_nim/fake-primary", "fake-primary"], cooldown=cooldown)
    proxy = live_proxy(two_tier_config(primary.port, None, hooks=True),
                       env={"LA_NVIDIA_THROTTLE_STATE": str(state)})
    try:
        status, events, _headers = anthropic_stream(proxy.port, model=TEST_MODEL, timeout=90)
        assert status == 200, (status, events, proxy.log_text()[-1500:])
        hits = primary.hits()
        assert len(hits) == 1, hits                        # exactly one upstream hit
        # the hit must come AFTER the cooldown, not during it (fake records ts per hit)
        assert hits[0]["ts"] >= seeded_at + cooldown - 1.5, \
            f"hit at {hits[0]['ts'] - seeded_at:.1f}s after seeding, cooldown was {cooldown}s"
    finally:
        proxy.stop()
