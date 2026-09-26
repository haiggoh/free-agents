#!/usr/bin/env python3
"""Tests for bin/la_proxy_hooks.py. Must run under the LiteLLM interpreter:

  ~/.local/pipx/venvs/litellm/bin/python tests/test_la_proxy_hooks.py

(skips cleanly when LiteLLM is not importable). Each fix is tested by OUTCOME against the real
installed adapter, and the stream fix is mutation-tested: with the patch removed the same
stream must reproduce the exact Claude Code failure, or the test proves nothing.
"""
import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("LA_NVIDIA_THROTTLE_STATE", os.path.join(tempfile.mkdtemp(), "state"))

try:
    import litellm  # noqa: F401
    HAVE_LITELLM = True
except Exception:
    HAVE_LITELLM = False


def load_hooks():
    spec = importlib.util.spec_from_file_location("la_proxy_hooks", REPO / "bin" / "la_proxy_hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ch(content=None, reasoning=None, finish=None):
    from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices
    return ModelResponseStream(choices=[StreamingChoices(
        index=0, delta=Delta(content=content, reasoning_content=reasoning), finish_reason=finish)])


def mismatches(chunks):
    from litellm.llms.anthropic.experimental_pass_through.adapters.streaming_iterator import (
        AnthropicStreamWrapper)
    open_types, errors, text, thinking = {}, [], "", ""
    for ev in AnthropicStreamWrapper(completion_stream=iter(chunks), model="m"):
        if ev.get("type") == "content_block_start":
            open_types[ev["index"]] = ev["content_block"]["type"]
        if ev.get("type") == "content_block_delta":
            dt, bt = ev["delta"]["type"], open_types.get(ev["index"])
            if (dt == "thinking_delta") != (bt == "thinking"):
                errors.append(f"{dt} into {bt}")
            text += ev["delta"].get("text", "")
            thinking += ev["delta"].get("thinking", "")
    return errors, text, thinking


MIXED = lambda: [ch(reasoning="plan"), ch(content="Hi", reasoning=" done"),
                 ch(content="!"), ch(finish="stop")]


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class StreamSplitTests(unittest.TestCase):
    def test_mixed_chunk_no_longer_breaks_and_loses_nothing(self):
        load_hooks()
        errors, text, thinking = mismatches(MIXED())
        self.assertEqual(errors, [])
        self.assertEqual(text, "Hi!")
        self.assertEqual(thinking, "plan done")

    def test_mixed_first_chunk(self):
        load_hooks()
        errors, text, thinking = mismatches(
            [ch(content="\n", reasoning="a"), ch(reasoning="b"), ch(content="Hi"), ch(finish="stop")])
        self.assertEqual(errors, [])
        self.assertEqual(thinking, "ab")

    def test_mutation_unpatched_adapter_reproduces_the_bug(self):
        from litellm.llms.anthropic.experimental_pass_through.adapters import streaming_iterator as si
        load_hooks()
        patched = si._CombinedChunkSplitter.__dict__["_split"]
        orig = patched.__func__.__closure__
        # Find the original function captured by the wrapper and restore it temporarily.
        original = next(c.cell_contents for c in orig if callable(c.cell_contents)
                        and c.cell_contents.__name__ == "_split")
        si._CombinedChunkSplitter._split = staticmethod(original)
        try:
            errors, _, _ = mismatches(MIXED())
            self.assertIn("thinking_delta into text", errors)
        finally:
            si._CombinedChunkSplitter._split = patched

    def test_patch_is_idempotent(self):
        mod = load_hooks()
        self.assertTrue(mod.install_stream_split_patch())
        self.assertTrue(mod.install_stream_split_patch())
        errors, text, _ = mismatches(MIXED())
        self.assertEqual((errors, text), ([], "Hi!"))   # not double-split / duplicated


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class NvidiaKwargsTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_hooks()

    def test_stop_sequences_become_stop_and_safeguards_dropped(self):
        out = self.mod.normalize_nvidia_kwargs(
            {"model": "nvidia_nim/x", "stop_sequences": ["</severity>"], "safeguards": {"a": 1}})
        self.assertEqual(out.get("stop"), ["</severity>"])
        self.assertNotIn("stop_sequences", out)
        self.assertNotIn("safeguards", out)

    def test_existing_stop_wins(self):
        out = self.mod.normalize_nvidia_kwargs({"stop": ["X"], "stop_sequences": ["Y"]})
        self.assertEqual(out["stop"], ["X"])

    def test_adapter_really_forwards_the_rejected_keys(self):
        # Guards the premise: if a future LiteLLM translates these itself, this fails and the
        # normalisation can be retired rather than kept on faith.
        from litellm.llms.anthropic.experimental_pass_through.adapters.transformation import (
            LiteLLMAnthropicMessagesAdapter)
        out = LiteLLMAnthropicMessagesAdapter().translate_anthropic_to_openai(
            anthropic_message_request={"model": "m", "max_tokens": 8, "stop_sequences": ["S"],
                                       "safeguards": {}, "messages": [{"role": "user", "content": "x"}]})
        keys = set(dict(out[0] if isinstance(out, tuple) else out))
        self.assertTrue({"stop_sequences", "safeguards"} & keys)

    def test_hook_only_touches_nvidia_and_takes_a_token(self):
        hooks = self.mod.LAProxyHooks()
        with tempfile.TemporaryDirectory() as d:
            from rate_limiter import FileTokenBucket
            hooks._bucket = FileTokenBucket(os.path.join(d, "s"), rpm=40)
            other = asyncio.run(hooks.async_pre_call_deployment_hook({"model": "gemini/x"}, None))
            self.assertIsNone(other)
            self.assertEqual(hooks._bucket.status()["tokens"], 40)
            out = asyncio.run(hooks.async_pre_call_deployment_hook(
                {"model": "nvidia_nim/x", "safeguards": 1}, None))
            self.assertNotIn("safeguards", out)
            self.assertLess(hooks._bucket.status()["tokens"], 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
