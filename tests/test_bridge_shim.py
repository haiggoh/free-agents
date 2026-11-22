#!/usr/bin/env python3
"""Task 2 Step 2 — install_bridge_shim() unit tests (litellm venv gate).

The shim mirrors upstream PR #53341 (BerriAI/litellm#53340 fix) AT RUNTIME, only when the
installed LiteLLM lacks the guard — it must self-retire on an upgraded LiteLLM and never
touch the pipx venv. These tests pin: the wrap raises APIConnectionError(llm_provider=
"anthropic") on a None stream, passes a REAL stream through as an AnthropicSSEStream,
detects an upstream guard by source inspection without wrapping, and is idempotent.
"""
import inspect
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))

import la_proxy_hooks  # noqa: E402

litellm = pytest.importorskip("litellm")


class _FakeGuardedAdapter:
    """Stands in for an upgraded LiteLLM whose funnel already carries the #53341 guard."""

    def translate_completion_output_params_streaming(self, completion_stream, model):
        if completion_stream is None:      # the exact upstream guard string, verbatim
            raise ValueError("upstream guard fired")
        return completion_stream


class _FakeUnguardedAdapter:
    def translate_completion_output_params_streaming(self, completion_stream, model):
        return completion_stream


def test_install_on_this_litellm_installs_guard():
    # 1.110.2 verified by source grep: no "completion_stream is None" under llms/anthropic
    assert la_proxy_hooks.BRIDGE_SHIM_STATE == "installed"
    adapter = la_proxy_hooks.litellm_adapter("AnthropicAdapter")()
    with pytest.raises(litellm.APIConnectionError) as ei:
        adapter.translate_completion_output_params_streaming(None, model="m")
    assert getattr(ei.value, "llm_provider", None) == "anthropic", ei.value


def test_real_stream_still_returns_anthropic_sse_stream():
    adapter = la_proxy_hooks.litellm_adapter("AnthropicAdapter")()
    chunks = iter(la_proxy_hooks._probe_chunks()["mixed transition"])
    out = adapter.translate_completion_output_params_streaming(chunks, model="m")
    assert type(out).__name__ == "AnthropicSSEStream", type(out)


def test_upstream_guard_detected_and_not_wrapped(monkeypatch):
    monkeypatch.setattr(la_proxy_hooks, "BRIDGE_SHIM_STATE", "")
    monkeypatch.setattr(la_proxy_hooks, "litellm_adapter", lambda name: _FakeGuardedAdapter)
    state = la_proxy_hooks.install_bridge_shim()
    assert state == "upstream-guard-present", state
    # detection by source inspection must NOT have wrapped the fake
    fn = _FakeGuardedAdapter.translate_completion_output_params_streaming
    assert not getattr(fn, "_la_bridge_shim", False), inspect.getsource(fn)


def test_unguarded_fake_gets_wrapped(monkeypatch):
    monkeypatch.setattr(la_proxy_hooks, "BRIDGE_SHIM_STATE", "")
    monkeypatch.setattr(la_proxy_hooks, "litellm_adapter", lambda name: _FakeUnguardedAdapter)
    state = la_proxy_hooks.install_bridge_shim()
    assert state == "installed", state
    fn = _FakeUnguardedAdapter.translate_completion_output_params_streaming
    assert getattr(fn, "_la_bridge_shim", False), "fake funnel was not wrapped"


def test_install_twice_does_not_double_wrap():
    cls = la_proxy_hooks.litellm_adapter("AnthropicAdapter")
    before = cls.translate_completion_output_params_streaming
    first = la_proxy_hooks.install_bridge_shim()
    second = la_proxy_hooks.install_bridge_shim()
    assert first == second == la_proxy_hooks.BRIDGE_SHIM_STATE
    assert cls.translate_completion_output_params_streaming is before, \
        "a second install re-wrapped the funnel"
