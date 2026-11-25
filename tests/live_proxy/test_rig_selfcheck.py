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


# ---- R3 Task 1: the two NIM shapes (toolburst / toolcut) + has_hint in the hit log --------

import urllib.request


def _post_stream(fake, body):
    """POST straight to the fake (no proxy) and collect parsed SSE chunks.
    Returns (chunks, error_class_or_None, saw_done) — a hard close may read as a clean
    EOF (HTTP/1.0), so 'no [DONE]' is the discriminator, not a read error."""
    req = urllib.request.Request(f"http://127.0.0.1:{fake.port}/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    chunks = []
    saw_done = False
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data:"):
                    if line[5:].strip() == "[DONE]":
                        saw_done = True
                    elif line[5:].strip():
                        chunks.append(json.loads(line[5:]))
        return chunks, None, saw_done
    except Exception as exc:
        return chunks, type(exc).__name__, saw_done


def _stream_body():
    return {"model": "fake-primary", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}


def test_toolburst_delivers_one_full_tool_call_chunk(fake_upstream):
    fake = fake_upstream("toolburst")
    chunks, err, _saw_done = _post_stream(fake, _stream_body())
    assert err is None, err
    tc = [t for ch in chunks for t in
          ((ch.get("choices") or [{}])[0].get("delta") or {}).get("tool_calls") or []]
    assert len(tc) == 1, f"expected exactly ONE tool_call chunk (NIM buffers all arguments): {chunks}"
    args = tc[0]["function"]["arguments"]
    assert len(args) >= 6000, f"the whole call must arrive in one chunk, got {len(args)} chars"
    finishes = [(ch.get("choices") or [{}])[0].get("finish_reason") for ch in chunks]
    assert "tool_calls" in finishes, finishes


def test_toolcut_closes_without_any_finish(fake_upstream):
    fake = fake_upstream("toolcut")
    chunks, err, saw_done = _post_stream(fake, _stream_body())
    finishes = [(ch.get("choices") or [{}])[0].get("finish_reason") for ch in chunks]
    assert all(f is None for f in finishes), f"toolcut must close with NO finish_reason: {finishes}"
    assert not saw_done, "toolcut must close with NO [DONE] marker (hard cut)"
    reasoning = [ch for ch in chunks
                 if ((ch.get("choices") or [{}])[0].get("delta") or {}).get("reasoning_content")]
    assert reasoning, "toolcut streams reasoning chunks first, then dies"


def test_hit_log_records_has_hint(fake_upstream):
    fake = fake_upstream("ok")
    _post_stream(fake, _stream_body())                                   # no hint in messages
    body2 = {"model": "fake-primary", "stream": False,
             "messages": [{"role": "assistant",
                           "content": "[free-agents] The provider closed this response mid-stream (x, 3 s)."}]}
    _post_stream(fake, body2)                                            # hint present
    hits = fake.hits()
    assert len(hits) == 2, hits
    assert hits[0].get("has_hint") is False, hits
    assert hits[1].get("has_hint") is True, hits
