#!/usr/bin/env python3
"""Tests for bin/la_proxy_hooks.py. Must run under the LiteLLM interpreter:

  ~/.local/pipx/venvs/litellm/bin/python tests/test_la_proxy_hooks.py

(skips cleanly when LiteLLM is not importable). Each fix is tested by OUTCOME against the real
installed adapter, and the stream check is mutation-tested: a planted buggy adapter must make it
warn, or a passing check proves nothing.
"""
import asyncio
import contextlib
import io
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
class StreamAdapterTests(unittest.TestCase):
    """The 0.19.10 split patch is retired: LiteLLM >= 1.102.1 fixed the mixed-chunk bug."""

    def test_installed_adapter_handles_mixed_chunks_unpatched(self):
        # Guards the retirement: the REAL adapter, with nothing of ours patched in, must type
        # mixed chunks correctly. If an older LiteLLM comes back, this fails.
        errors, text, thinking = mismatches(MIXED())
        self.assertEqual(errors, [])
        self.assertEqual(text, "Hi!")
        self.assertEqual(thinking, "plan done")
        errors, _, thinking = mismatches(
            [ch(content="\n", reasoning="a"), ch(reasoning="b"), ch(content="Hi"), ch(finish="stop")])
        self.assertEqual((errors, thinking), ([], "ab"))

    def test_module_no_longer_patches_litellm(self):
        from litellm.llms.anthropic.experimental_pass_through.adapters import streaming_iterator as si
        before = si._CombinedChunkSplitter.__dict__.get("_split")
        load_hooks()
        self.assertIs(si._CombinedChunkSplitter.__dict__.get("_split"), before)

    def test_check_passes_on_fixed_adapter(self):
        mod = load_hooks()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertTrue(mod.check_stream_adapter())
        self.assertEqual(err.getvalue(), "")

    def test_mutation_check_warns_on_buggy_adapter(self):
        # Planted positive: simulate the 1.91 bug (a thinking_delta inside a text block) and the
        # check must fail LOUDLY with the upgrade hint -- otherwise a passing check proves nothing.
        mod = load_hooks()
        mod.stream_mismatches = lambda chunks: (["thinking_delta into text"], "", "")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(mod.check_stream_adapter())
        self.assertIn("still has the mixed-chunk bug", err.getvalue())
        self.assertIn("pipx upgrade litellm", err.getvalue())

    def test_check_never_raises(self):
        mod = load_hooks()
        def boom(chunks):
            raise RuntimeError("adapter moved")
        mod.stream_mismatches = boom
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(mod.check_stream_adapter())
        self.assertIn("could not probe", err.getvalue())


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
        # Since LiteLLM 1.102.1 stop_sequences arrives already translated to stop; safeguards is
        # still forwarded verbatim and NIM 400s on it, so the drop must stay.
        self.assertIn("safeguards", keys)

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
