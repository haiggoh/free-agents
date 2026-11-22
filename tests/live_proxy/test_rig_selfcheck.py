#!/usr/bin/env python3
"""Task 1 Step 1 — the live-proxy rig's own self-checks (RED before the rig exists).

These run REAL LiteLLM against a loopback fake OpenAI upstream. They are the delivery
proof harness for Tasks 2, 5 and 6: every later behavioural claim (429-not-500 mapping,
bridge shim, per-provider bucket rpm, cooling models) is proven end to end through this
rig, so the rig must first prove that it

  1. round-trips a streaming /v1/messages request through real LiteLLM to the fake
     (and that the fake is hit on the chat-completions path with the production
     ``nvidia_nim/`` prefix — the ``openai/`` prefix would exercise a different bridge),
  2. never points a proxy child at the live limiter state
     (``~/.claude/free-agents`` — plan rule 8: a test that can read it is a bug), and
  3. never binds inside the live port band (<= 4199).

Collected ONLY when FA_LIVE_PROXY_GATE=1 (see conftest.py here): the dedicated
.rtc/pre-flight.sh gate runs them serially; the main suite (-n 12) must not.
"""
import json

NVIDIA_MODEL = "claude-opus-5-5"


def test_ok_stream_round_trips(fake_upstream, live_proxy, anthropic_stream, two_tier_config):
    fake = fake_upstream("ok")
    proxy = live_proxy(two_tier_config(fake.port, None, hooks=False))
    try:
        status, events, _headers = anthropic_stream(proxy.port, model=NVIDIA_MODEL)
        assert status == 200, (status, events)
        text = "".join(e.get("delta", {}).get("text") or ""
                       for e in events if e.get("type") == "content_block_delta")
        assert text == "pong-ok", events
        hits = fake.hits()
        assert len(hits) == 1, hits
        assert hits[0]["path"] == "/v1/chat/completions", hits
        assert hits[0]["model"] == "fake-primary", hits
    finally:
        proxy.stop()


def test_rig_never_uses_live_state(fake_upstream, live_proxy, two_tier_config):
    fake = fake_upstream("ok")
    proxy = live_proxy(two_tier_config(fake.port, None, hooks=True))
    try:
        env = json.loads(proxy.env_json.read_text())
        assert env.get("LA_NVIDIA_THROTTLE_STATE"), env.keys()
        assert env.get("FA_LIMITER_DIR"), env.keys()
        for key, value in env.items():
            assert ".claude/free-agents" not in str(value), (key, value)
    finally:
        proxy.stop()


def test_rig_port_outside_live_band(fake_upstream, live_proxy, two_tier_config):
    fake = fake_upstream("ok")
    proxy = live_proxy(two_tier_config(fake.port, None, hooks=True))
    try:
        assert proxy.port > 4199, proxy.port
        assert fake.port > 4199, fake.port
    finally:
        proxy.stop()
