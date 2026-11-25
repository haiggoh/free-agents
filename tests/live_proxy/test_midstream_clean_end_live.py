#!/usr/bin/env python3
"""tests/live_proxy/test_midstream_clean_end_live.py — R3 Task 2 live proof over REAL LiteLLM.

An upstream stream that dies AFTER >=1 chunk must end the Anthropic stream VALIDLY (complete
message, stop_reason end_turn, last text block starts "[free-agents] The provider closed"), never
a 500. Modes: toolcut (reasoning then hard close -> SPLIT), dier (same, -> SPLIT), toolburst with
silence > stream_timeout (LiteLLM's own timeout fires mid-silence -> SPLIT), die (text then close
-> GENERIC).
"""
NVIDIA_MODEL = "claude-opus-5-5"


def _texts(events):
    return "".join(e.get("delta", {}).get("text") or ""
                   for e in events if e.get("type") == "content_block_delta")


def _assert_clean_end(status, events, expect_split, proxy=None):
    assert status == 200, (status, events)
    types = [e.get("type") for e in events]
    assert types[-1] == "message_stop", types
    md = [e for e in events if e.get("type") == "message_delta"]
    assert md and (md[-1].get("delta") or {}).get("stop_reason") == "end_turn", events
    text = _texts(events)
    # the hint is the FINAL text block (partial text may precede it — that is the point of GENERIC)
    assert "[free-agents] The provider closed" in text, text
    if expect_split:
        assert "write large files in parts" in text, text
        assert text.endswith("Do not retry the same oversized call."), text
    else:
        assert 'reply "continue" to resume' in text, text
        assert text.endswith('reply "continue" to resume.'), text
    if proxy is not None:
        assert "mid-stream death after" in proxy.log_text(), proxy.log_text()[-800:]


def test_toolcut_clean_end_split(fake_upstream, live_proxy, anthropic_stream, two_tier_config):
    up = fake_upstream("toolcut")
    proxy = live_proxy(two_tier_config(up.port, None, hooks=True))
    try:
        up.set_mode("toolcut 2")
        status, events, _ = anthropic_stream(proxy.port, NVIDIA_MODEL, timeout=30)
        _assert_clean_end(status, events, expect_split=True, proxy=proxy)
    finally:
        proxy.stop()


def test_dier_clean_end_split(fake_upstream, live_proxy, anthropic_stream, two_tier_config):
    up = fake_upstream("dier")
    proxy = live_proxy(two_tier_config(up.port, None, hooks=True))
    try:
        status, events, _ = anthropic_stream(proxy.port, NVIDIA_MODEL, timeout=30)
        _assert_clean_end(status, events, expect_split=True)
    finally:
        proxy.stop()


def test_toolburst_stream_timeout_clean_end_split(fake_upstream, live_proxy, anthropic_stream,
                                                    two_tier_config):
    up = fake_upstream("toolburst")
    proxy = live_proxy(two_tier_config(up.port, None, hooks=True, stream_timeout=3))
    try:
        up.set_mode("toolburst 5")   # silence 5 s > stream_timeout 3 s -> LiteLLM cuts mid-silence
        status, events, _ = anthropic_stream(proxy.port, NVIDIA_MODEL, timeout=30)
        _assert_clean_end(status, events, expect_split=True)
    finally:
        proxy.stop()


def test_die_after_text_clean_end_generic(fake_upstream, live_proxy, anthropic_stream,
                                          two_tier_config):
    up = fake_upstream("die")
    proxy = live_proxy(two_tier_config(up.port, None, hooks=True))
    try:
        status, events, _ = anthropic_stream(proxy.port, NVIDIA_MODEL, timeout=30)
        _assert_clean_end(status, events, expect_split=False)
    finally:
        proxy.stop()
