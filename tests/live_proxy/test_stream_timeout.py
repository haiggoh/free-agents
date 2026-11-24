#!/usr/bin/env python3
"""0.63.2 hotfix — a HUNG upstream fails over to the fallback instead of hanging forever.

Incident 2026-11-24: NVIDIA `z-ai/glm-5.3-flash` was catalog-listed but answered NOTHING (a
16-token direct request got http=000 after 90 s; Le Chonk on the same key answered in <1 s).
remote-session.sh wrote no timeout on the main deployments, so a GLM session sat 18 minutes
with zero replies and never reached its fallbacks. The fix writes a per-deployment
`stream_timeout` and `TimeoutErrorRetries: 0`. These tests run REAL LiteLLM against the fake
upstream's `hang` mode (accepts the request, sends nothing), with the config shape the
generator now writes.
"""
import time

TEST_MODEL = "claude-opus-5-5"


def _text(events) -> str:
    out = []
    for e in events:
        d = e.get("delta") or {}
        if isinstance(d, dict) and d.get("type") == "text_delta":
            out.append(d.get("text", ""))
    return "".join(out)


def test_hung_primary_fails_over_within_stream_timeout(fake_upstream, live_proxy,
                                                       anthropic_stream, two_tier_config):
    primary, fallback = fake_upstream("hang"), fake_upstream("ok")
    proxy = live_proxy(two_tier_config(primary.port, fallback.port, hooks=True,
                                       stream_timeout=2))
    t0 = time.monotonic()
    status, events, _ = anthropic_stream(proxy.port, TEST_MODEL, timeout=40)
    elapsed = time.monotonic() - t0
    assert status == 200, (status, events)
    assert "pong" in _text(events) or "ng-ok" in _text(events), events
    assert elapsed < 30, f"failover took {elapsed:.1f}s; stream_timeout=2 should bound it"
    assert primary.hits(), "the hung primary was never even tried"
    assert fallback.hits(), "the fallback never received the request"


def test_time_queued_in_the_limiter_is_not_counted_as_a_hang(fake_upstream, live_proxy,
                                                             anthropic_stream, two_tier_config,
                                                             tmp_path):
    # User requirement 2026-11-24: time a request spends QUEUED in our own rate limiter must
    # never count as the model not responding. The limiter waits in the pre-call hook BEFORE
    # LiteLLM opens the upstream connection; stream_timeout is a read timeout on that
    # connection. Seed: a cooling primary with NO fallback chain, which WAITS out its cooldown
    # in the bucket queue (the mechanism test_error_mapping's chain-less test proves). With
    # stream_timeout=2 and a 6 s queue, counting the queue would end the request with an error
    # (there is nowhere to fail over to); not counting it gives exactly one hit, after the
    # cooldown, and a 200.
    from test_error_mapping import _seed_cooling
    primary = fake_upstream("ok")
    state = tmp_path / "cooling-state"
    cooldown = 6.0
    seeded_at = time.time()
    _seed_cooling(state, ["nvidia_nim/fake-primary", "fake-primary"], cooldown=cooldown)
    proxy = live_proxy(two_tier_config(primary.port, None, hooks=True, stream_timeout=2),
                       env={"LA_NVIDIA_THROTTLE_STATE": str(state)})
    status, events, _ = anthropic_stream(proxy.port, TEST_MODEL, timeout=60)
    assert status == 200, ("queue time was counted as a hang", status, events,
                           proxy.log_text()[-1200:])
    hits = primary.hits()
    assert len(hits) == 1, hits
    assert hits[0]["ts"] >= seeded_at + cooldown - 1.5, (
        f"hit at {hits[0]['ts'] - seeded_at:.1f}s: the request did not actually queue")


def test_without_stream_timeout_the_hang_is_silent(fake_upstream, live_proxy, anthropic_stream,
                                                   two_tier_config):
    # The negative control: the SAME topology without the setting gives the client nothing
    # within its own deadline, so the setting is what makes the failover happen (and the fake's
    # hang mode really hangs).
    primary, fallback = fake_upstream("hang"), fake_upstream("ok")
    proxy = live_proxy(two_tier_config(primary.port, fallback.port, hooks=True))
    t0 = time.monotonic()
    try:
        status, events, _ = anthropic_stream(proxy.port, TEST_MODEL, timeout=8)
    except Exception as exc:          # socket.timeout / URLError: no answer, as expected
        assert time.monotonic() - t0 >= 7, f"failed fast for another reason: {exc!r}"
        assert not fallback.hits(), "without a timeout the fallback must never be reached"
        return
    raise AssertionError(f"got an answer without stream_timeout: {status} {events[:2]}")
