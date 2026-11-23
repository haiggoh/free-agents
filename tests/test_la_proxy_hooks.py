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
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("LA_NVIDIA_THROTTLE_STATE", os.path.join(tempfile.mkdtemp(), "state"))
# Fix 8 reads the retired list; tests here that are not about it must not see the REAL one (it
# names ids they use as healthy models). An absent file = empty list (fail-open). Scoped to THIS
# module: a module-level os.environ write leaks into every later test of the same xdist worker
# (measured 2026-11-23: 8 picker/fallback tests elsewhere saw an empty retired list).
_ENV_PATCH = unittest.mock.patch.dict(
    os.environ, {"LA_BROKEN_MODELS_FILE": os.path.join(tempfile.mkdtemp(), "none.json")})


def setUpModule():
    _ENV_PATCH.start()


def tearDownModule():
    _ENV_PATCH.stop()

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
    # The module's own probe, so the test and the proxy share ONE lookup of LiteLLM's adapter.
    return load_hooks().stream_mismatches(chunks)


def MIXED():
    return [ch(reasoning="plan"), ch(content="Hi", reasoning=" done"),
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
        splitter = load_hooks().litellm_adapter("_CombinedChunkSplitter")
        before = splitter.__dict__.get("_split")
        load_hooks()
        self.assertIs(splitter.__dict__.get("_split"), before)

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
class AdapterLookupTests(unittest.TestCase):
    """LiteLLM relocates its private adapter package between releases; the lookup must follow."""

    def setUp(self):
        self.mod = load_hooks()

    def test_every_adapter_class_resolves_on_this_litellm(self):
        for name in self.mod._ADAPTER_MODULES:
            self.assertEqual(self.mod.litellm_adapter(name).__name__, name)

    def test_unknown_parent_is_found_by_walking(self):
        # Planted relocation: no known parent matches, so only the package walk can find it.
        self.mod._ADAPTER_PARENTS = ("no_such_parent_a", "no_such_parent_b")
        cls = self.mod.litellm_adapter("AnthropicStreamWrapper")
        self.assertEqual(cls.__name__, "AnthropicStreamWrapper")

    def test_removed_class_raises_import_error(self):
        self.mod._ADAPTER_MODULES = {"GoneWrapper": "adapters.streaming_iterator"}
        with self.assertRaises(ImportError):
            self.mod.litellm_adapter("GoneWrapper")


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
        out = self.mod.litellm_adapter("LiteLLMAnthropicMessagesAdapter")().translate_anthropic_to_openai(
            anthropic_message_request={"model": "m", "max_tokens": 8, "stop_sequences": ["S"],
                                       "safeguards": {}, "messages": [{"role": "user", "content": "x"}]})
        keys = set(dict(out[0] if isinstance(out, tuple) else out))
        # Since LiteLLM 1.102.1 stop_sequences arrives already translated to stop; safeguards is
        # still forwarded verbatim and NIM 400s on it, so the drop must stay.
        self.assertIn("safeguards", keys)

    def test_hook_only_touches_nvidia_and_takes_a_slot(self):
        hooks = self.mod.LAProxyHooks()
        with tempfile.TemporaryDirectory() as d:
            from rate_limiter import SlidingWindowLimiter
            hooks._bucket = SlidingWindowLimiter(os.path.join(d, "s"), rpm=40)
            other = asyncio.run(hooks.async_pre_call_deployment_hook({"model": "gemini/x"}, None))
            self.assertIsNone(other)
            self.assertEqual(hooks._bucket.status()["in_window"], 0)
            out = asyncio.run(hooks.async_pre_call_deployment_hook(
                {"model": "nvidia_nim/x", "safeguards": 1}, None))
            self.assertNotIn("safeguards", out)
            self.assertEqual(hooks._bucket.status()["in_window"], 1)


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class Upstream429Tests(unittest.TestCase):
    """A real litellm.RateLimitError from NVIDIA is CLASSIFIED (0.40.0): a near-empty window
    is the provider's per-MODEL throttle -- only that model cools, every other proxy runs on;
    a window at least half full is OUR RPM overrun -- every proxy on the file pauses."""

    def setUp(self):
        self.mod = load_hooks()
        self.dir = tempfile.TemporaryDirectory()
        from rate_limiter import SlidingWindowLimiter
        self.path = os.path.join(self.dir.name, "s")
        self.hooks = self.mod.LAProxyHooks()
        self.hooks._bucket = SlidingWindowLimiter(self.path, rpm=40, cooldown=10)

    def tearDown(self):
        self.dir.cleanup()

    def _fail(self, exc, model="nvidia_nim/x"):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            asyncio.run(self.hooks.async_log_failure_event(
                {"model": model, "exception": exc}, None, None, None))
        return err.getvalue()

    def _rate_limit_error(self, headers=None):
        import httpx
        import litellm
        resp = httpx.Response(429, headers=headers or {},
                              request=httpx.Request("POST", "https://integrate.api.nvidia.com"))
        return litellm.RateLimitError(message="Too Many Requests", llm_provider="nvidia_nim",
                                      model="x", response=resp)

    def test_near_empty_429_cools_only_that_model(self):
        from rate_limiter import SlidingWindowLimiter
        for _ in range(3):
            self.hooks._bucket.try_acquire()
        log = self._fail(self._rate_limit_error())
        # 3 slots we took + the SDK's 2 hidden retries of the failed call
        self.assertIn("5/40", log)
        self.assertIn("model throttle", log)
        other = SlidingWindowLimiter(self.path, rpm=40)       # a second proxy's view
        self.assertEqual(other.try_acquire(), 0.0)            # NOT paused machine-wide
        self.assertGreater(other.model_cooling("nvidia_nim/x"), 9.0)

    def test_overrun_429_pauses_every_proxy(self):
        from rate_limiter import SlidingWindowLimiter
        for _ in range(20):
            self.hooks._bucket.try_acquire()
        log = self._fail(self._rate_limit_error())
        self.assertIn("RPM overrun", log)
        other = SlidingWindowLimiter(self.path, rpm=40)
        self.assertGreater(other.status()["cooldown_remaining"], 9.0)
        self.assertGreater(other.try_acquire(), 0.0)

    def test_429_uses_retry_after_header(self):
        self._fail(self._rate_limit_error({"retry-after": "30"}))
        self.assertGreater(self.hooks._bucket.model_cooling("nvidia_nim/x"), 29.0)

    def test_non_429_and_non_nvidia_do_not_pause(self):
        import litellm
        self._fail(litellm.BadRequestError(message="bad", model="x", llm_provider="nvidia_nim"))
        self._fail(self._rate_limit_error(), model="gemini/x")
        self.assertEqual(self.hooks._bucket.status()["cooldown_remaining"], 0.0)
        self.assertEqual(self.hooks._bucket.model_cooling("nvidia_nim/x"), 0.0)


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class PerModelFailoverTests(unittest.TestCase):
    """0.40.0: a cooling model fails over AT ONCE (pre-call hook raises, no 40 RPM slot
    spent, zero upstream calls -- probed on LiteLLM 1.105.1) when the proxy has a fallback
    chain; with no chain it queues like today, and a completed success clears the entry."""

    def setUp(self):
        self.mod = load_hooks()
        self.dir = tempfile.TemporaryDirectory()
        from rate_limiter import SlidingWindowLimiter
        self.path = os.path.join(self.dir.name, "s")
        self.hooks = self.mod.LAProxyHooks()
        self.hooks._bucket = SlidingWindowLimiter(self.path, rpm=40, cooldown=10)
        os.environ.pop("LA_PROXY_FALLBACK_NAMES", None)

    def tearDown(self):
        os.environ.pop("LA_PROXY_FALLBACK_NAMES", None)
        self.dir.cleanup()

    def test_cooling_model_fails_over_at_once_without_a_slot(self):
        import litellm
        self.hooks._bucket.note_429(model="nvidia_nim/z-ai/glm-5.3-flash")
        os.environ["LA_PROXY_FALLBACK_NAMES"] = "la-fallback-1"
        with self.assertRaises(litellm.RateLimitError):
            asyncio.run(self.hooks.async_pre_call_deployment_hook(
                {"model": "nvidia_nim/z-ai/glm-5.3-flash"}, None))
        # fast-fail must not book a 40 RPM slot: the fallback needs it
        self.assertEqual(self.hooks._bucket.status()["in_window"], 0)

    def test_cooling_model_without_a_chain_queues_like_today(self):
        self.hooks._bucket.note_429(model="nvidia_nim/z-ai/glm-5.3-flash")
        out = asyncio.run(self.hooks.async_pre_call_deployment_hook(
            {"model": "nvidia_nim/z-ai/glm-5.3-flash"}, None))
        self.assertNotIn("safeguards", out or {})          # the normal path ran
        self.assertEqual(self.hooks._bucket.status()["in_window"], 1)   # slot taken

    def test_other_model_is_unaffected(self):
        import litellm
        self.hooks._bucket.note_429(model="nvidia_nim/z-ai/glm-5.3-flash")
        os.environ["LA_PROXY_FALLBACK_NAMES"] = "la-fallback-1"
        out = asyncio.run(self.hooks.async_pre_call_deployment_hook(
            {"model": "nvidia_nim/deepseek-ai/deepseek-v4.1-flash"}, None))
        self.assertIsNotNone(out)                           # no raise: only the chain runs
        self.assertEqual(self.hooks._bucket.status()["in_window"], 1)

    def test_success_event_clears_the_model_entry(self):
        self.hooks._bucket.note_429(model="nvidia_nim/x")
        asyncio.run(self.hooks.async_log_success_event(
            {"model": "nvidia_nim/x"}, None, None, None))
        self.assertEqual(self.hooks._bucket.model_cooling("nvidia_nim/x"), 0.0)

    def test_success_event_resets_the_global_counter(self):
        from rate_limiter import SlidingWindowLimiter
        for _ in range(20):
            self.hooks._bucket.try_acquire()
        self._fail_429()                                    # overrun: machine-wide
        self.assertEqual(self.hooks._bucket.status()["consecutive_429s"], 1)
        asyncio.run(self.hooks.async_log_success_event(
            {"model": "nvidia_nim/x"}, None, None, None))
        self.assertEqual(SlidingWindowLimiter(self.path, rpm=40).status()["consecutive_429s"], 0)

    def _fail_429(self):
        import httpx
        import litellm
        resp = httpx.Response(429, request=httpx.Request("POST", "https://integrate.api.nvidia.com"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            asyncio.run(self.hooks.async_log_failure_event(
                {"model": "nvidia_nim/x",
                 "exception": litellm.RateLimitError(message="Too Many Requests",
                     llm_provider="nvidia_nim", model="x", response=resp)}, None, None, None))
        return err.getvalue()


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class OutputCapTests(unittest.TestCase):
    """Fix 4: a provider's own 400 sets its output ceiling; nothing else lowers max_tokens.

    The text is Groq's REAL rejection of max_tokens=128000 (probed 2026-10-08). The failure side
    sees the bare model id with custom_llm_provider, the pre-call side sees "groq/<id>" -- the
    measured shapes from a live proxy, which is why both are exercised here.
    """
    GROQ_400 = ('GroqException - {"error": {"message": "`max_tokens` must be less than or equal '
                'to `16384`, the maximum value for `max_tokens` is less than the `context_window` '
                'for this model", "type": "invalid_request_error", "param": "max_tokens"}}')

    def setUp(self):
        self.mod = load_hooks()
        self.hooks = self.mod.LAProxyHooks()
        self.hooks._bucket = None

    def _bad_request(self, text):
        import litellm
        return litellm.BadRequestError(message=text, model="qwen/qwen3.8-27b", llm_provider="groq")

    def _fail(self, exc):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            asyncio.run(self.hooks.async_log_failure_event(
                {"model": "qwen/qwen3.8-27b", "custom_llm_provider": "groq", "exception": exc},
                None, None, None))
        return err.getvalue()

    def _pre(self, max_tokens, model="groq/qwen/qwen3.8-27b", provider="groq"):
        out = asyncio.run(self.hooks.async_pre_call_deployment_hook(
            {"model": model, "custom_llm_provider": provider, "max_tokens": max_tokens}, None))
        return None if out is None else out["max_tokens"]

    def test_nothing_is_clamped_before_the_provider_says_so(self):
        self.assertIsNone(self._pre(128000), "no guessed cap: an unseen model keeps the full ceiling")

    def test_groq_400_teaches_the_cap_and_later_requests_are_clamped(self):
        log = self._fail(self._bad_request(self.GROQ_400))
        self.assertIn("rejects max_tokens above 16384", log)
        self.assertEqual(self._pre(128000), 16384)
        self.assertIsNone(self._pre(8000), "a request already under the cap is left alone")
        # Other models are not affected by one model's cap.
        self.assertIsNone(self._pre(128000, model="groq/openai/gpt-oss-120b"))

    def test_unrelated_400_teaches_nothing(self):
        self._fail(self._bad_request("Unsupported parameter(s): `safeguards`"))
        self.assertIsNone(self._pre(128000))

    def test_openai_phrasing_is_recognised(self):
        exc = self._bad_request("max_tokens is too large: 128000. This model supports at most "
                                "4096 completion tokens, whereas you provided 128000.")
        self.assertEqual(self.mod.output_cap_from_error(exc), 4096)

    def test_nvidia_clamp_still_normalises_nim_keys(self):
        self.mod._LEARNED_CAPS["nvidia/x"] = 1000
        out = asyncio.run(self.hooks.async_pre_call_deployment_hook(
            {"model": "nvidia_nim/nvidia/x", "custom_llm_provider": "nvidia_nim",
             "max_tokens": 5000, "safeguards": 1}, None))
        self.assertEqual(out["max_tokens"], 1000)
        self.assertNotIn("safeguards", out)


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class LearnedRequestFixTests(unittest.TestCase):
    """Fix 5: a provider's 400 naming a field it rejects corrects that model's later requests.

    Every error string here is the provider's REAL reply, captured from a live proxy log on
    2026-10-20 (0.38.1). The router's one BadRequest retry makes the failing turn succeed.
    """
    GROQ = ('GroqException - {"error":{"message":"property \'prompt_cache_key\' is unsupported",'
            '"type":"invalid_request_error"}}')
    LLM7 = "OpenAIException - Model 'deepseek-v4-pro' does not support reasoning."
    ZAI = ("OpenAIException - This model always engages in thinking and cannot be disabled; "
           "please use low, high, or max")

    def setUp(self):
        self.mod = load_hooks()
        self.hooks = self.mod.LAProxyHooks()
        self.hooks._bucket = None

    def _fail(self, text, model, provider="openai"):
        import litellm
        exc = litellm.BadRequestError(message=text, model=model, llm_provider=provider)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            asyncio.run(self.hooks.async_log_failure_event(
                {"model": model, "custom_llm_provider": provider, "exception": exc}, None, None, None))
        return err.getvalue()

    def _pre(self, model, provider="openai", **params):
        kwargs = {"model": f"{provider}/{model}", "custom_llm_provider": provider, **params}
        out = asyncio.run(self.hooks.async_pre_call_deployment_hook(kwargs, None))
        return kwargs if out is None else out

    def test_groq_never_gets_prompt_cache_key_even_on_the_first_request(self):
        out = self._pre("openai/gpt-oss-120b", provider="groq", prompt_cache_key="k", max_tokens=9)
        self.assertNotIn("prompt_cache_key", out)
        self.assertEqual(out["max_tokens"], 9)

    def test_unsupported_property_is_learned_per_model(self):
        log = self._fail(self.GROQ, "qwen/q", provider="groq")
        self.assertIn("rejects 'prompt_cache_key'", log)
        self.assertNotIn("prompt_cache_key", self._pre("qwen/q", provider="groq", prompt_cache_key="k"))
        # A different model of another provider is untouched by groq's lesson.
        self.assertEqual(self._pre("m2", prompt_cache_key="k")["prompt_cache_key"], "k")

    def test_model_without_reasoning_loses_reasoning_effort(self):
        self.assertEqual(self._pre("deepseek-v4-pro", reasoning_effort="high")["reasoning_effort"], "high")
        log = self._fail(self.LLM7, "deepseek-v4-pro")
        self.assertIn("does not reason", log)
        out = self._pre("deepseek-v4-pro", reasoning_effort="high", thinking={"type": "enabled"})
        self.assertNotIn("reasoning_effort", out)
        self.assertNotIn("thinking", out)

    def test_always_thinking_model_never_gets_thinking_off(self):
        # Claude Code's thinking-disabled turns arrive as reasoning_effort="none" (measured
        # through LiteLLM 1.105.1 with an echo upstream).
        log = self._fail(self.ZAI, "glm-5.3-flash")
        self.assertIn("always thinks", log)
        self.assertEqual(self._pre("glm-5.3-flash", reasoning_effort="none")["reasoning_effort"], "low")
        self.assertEqual(self._pre("glm-5.3-flash", reasoning_effort="high")["reasoning_effort"], "high",
                         "an explicit level is left alone")

    def test_unrelated_400_learns_nothing(self):
        self._fail("max_tokens must be positive", "m3")
        self.assertEqual(self._pre("m3", reasoning_effort="none", prompt_cache_key="k"),
                         {"model": "openai/m3", "custom_llm_provider": "openai",
                          "reasoning_effort": "none", "prompt_cache_key": "k"})


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class TextOnlyFallbackTests(unittest.TestCase):
    """A text-only fallback gets the conversation WITHOUT its images instead of a 400.

    Incident 2026-11-23 (proxy-4143.log, 10:31:24): GLM 5.3 Flash 429'd; fallbacks 1-2 each
    answered this 400 for every turn, because the history carried a screenshot. No retry could
    ever succeed, so the session never recovered. A session that lost its images still works.
    """
    NIM = ("Nvidia_nimException - Received multimodal data but multimodal processing is not "
           "enabled. Use --enable-multimodal flag to enable multimodal processing.")
    PNG = {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}
    setUp, _fail, _pre = (LearnedRequestFixTests.setUp, LearnedRequestFixTests._fail,
                          LearnedRequestFixTests._pre)

    def _messages(self):
        return [
            {"role": "user", "content": [{"type": "text", "text": "look"},
                                         {"type": "image", "source": dict(self.PNG)}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                               "input": {"file_path": "shot.png"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                          "content": [{"type": "image", "source": dict(self.PNG)}]}]},
            {"role": "user", "content": [{"type": "image_url",
                                          "image_url": {"url": "data:image/png;base64,iVBO"}}]},
            {"role": "user", "content": "plain string content"},
        ]

    @staticmethod
    def _has_image(node) -> bool:
        if isinstance(node, dict):
            return node.get("type") in ("image", "image_url") or any(
                TextOnlyFallbackTests._has_image(v) for v in node.values())
        if isinstance(node, list):
            return any(TextOnlyFallbackTests._has_image(v) for v in node)
        return False

    def test_images_pass_through_before_any_refusal(self):
        out = self._pre("stepfun-ai/step-5-preview", provider="nvidia_nim", messages=self._messages())
        self.assertTrue(self._has_image(out["messages"]))

    def test_multimodal_refusal_strips_every_image_shape_for_that_model(self):
        log = self._fail(self.NIM, "stepfun-ai/step-5-preview", provider="nvidia_nim")
        self.assertIn("text-only", log)
        out = self._pre("stepfun-ai/step-5-preview", provider="nvidia_nim", messages=self._messages())
        msgs = out["messages"]
        self.assertFalse(self._has_image(msgs), "top-level, tool_result-nested and image_url all go")
        # Each image became a text note the model can read, so it knows one was there.
        self.assertEqual(msgs[0]["content"][0], {"type": "text", "text": "look"})
        self.assertEqual(msgs[0]["content"][1]["type"], "text")
        self.assertIn("image", msgs[0]["content"][1]["text"].lower())
        self.assertEqual(msgs[2]["content"][0]["type"], "tool_result")
        self.assertEqual(msgs[2]["content"][0]["content"][0]["type"], "text")
        self.assertEqual(msgs[3]["content"][0]["type"], "text")
        self.assertEqual(msgs[4]["content"], "plain string content")
        self.assertEqual(msgs[1], self._messages()[1], "non-image blocks are untouched")

    def test_strip_never_mutates_the_shared_request(self):
        # The router hands the same request to the primary and every fallback: an in-place
        # edit would blind an image-capable model later in the chain.
        self._fail(self.NIM, "stepfun-ai/step-5-preview", provider="nvidia_nim")
        msgs = self._messages()
        self._pre("stepfun-ai/step-5-preview", provider="nvidia_nim", messages=msgs)
        self.assertEqual(msgs, self._messages())

    def test_other_models_keep_their_images(self):
        self._fail(self.NIM, "stepfun-ai/step-5-preview", provider="nvidia_nim")
        out = self._pre("z-ai/glm-5.3-flash", provider="nvidia_nim", messages=self._messages())
        self.assertTrue(self._has_image(out["messages"]))

    def test_strip_applies_on_the_real_route_shape(self):
        # Measured through LiteLLM's HTTP layer (Router + fake NIM server, 2026-11-23): the
        # deployment hook gets model='nvidia_nim/<id>' with custom_llm_provider=None, while the
        # failure side reports '<id>' with the provider set. Both must reach the same key.
        self._fail(self.NIM, "stepfun-ai/step-5-preview", provider="nvidia_nim")
        kwargs = {"model": "nvidia_nim/stepfun-ai/step-5-preview", "messages": self._messages()}
        out = asyncio.run(self.hooks.async_pre_call_deployment_hook(kwargs, None)) or kwargs
        self.assertFalse(self._has_image(out["messages"]))


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class RetiredModelTests(unittest.TestCase):
    """A running proxy skips a fallback retired AFTER it started, with no upstream call.

    Incident 2026-11-23: proxy-4143 started 2026-11-17; deepseek-ai/deepseek-v4.1-flash was
    retired (410 EOL 2026-11-16) and dropped from the roster on 2026-11-18, but the proxy's
    frozen fallback chain kept calling it -- every turn, router retry included.
    """
    GONE_ID = "deepseek-ai/deepseek-v4.1-flash"
    NIM_410 = ("Nvidia_nimException - Error code: 410 - {'title': 'Gone', 'status': 410, 'detail': "
               "\"The model 'deepseek-ai/deepseek-v4.1-flash' has reached its end of life on "
               "2026-11-16T08:00:00Z and is no longer available.\"}")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.broken = self.tmp / "broken.json"
        self._write([])
        self.env = unittest.mock.patch.dict(os.environ, {"LA_BROKEN_MODELS_FILE": str(self.broken)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.mod = load_hooks()
        self.hooks = self.mod.LAProxyHooks()
        self.hooks._bucket = None

    def _write(self, ids, bump=0):
        import json
        self.broken.write_text(json.dumps({"models": [{"model_id": i, "status": "retired"} for i in ids]}))
        if bump:
            st = self.broken.stat()
            os.utime(self.broken, (st.st_atime, st.st_mtime + bump))

    def _pre(self, model):
        kwargs = {"model": f"nvidia_nim/{model}", "custom_llm_provider": "nvidia_nim", "max_tokens": 9}
        with contextlib.redirect_stderr(io.StringIO()) as err:
            out = asyncio.run(self.hooks.async_pre_call_deployment_hook(kwargs, None))
        return (kwargs if out is None else out), err.getvalue()

    def test_live_model_is_untouched(self):
        out, _ = self._pre("z-ai/glm-5.3-flash")
        self.assertEqual(out["model"], "nvidia_nim/z-ai/glm-5.3-flash")

    def test_listed_model_is_skipped_on_the_real_route_shape(self):
        # The real deployment hook sees custom_llm_provider=None (see TextOnlyFallbackTests).
        self._write([self.GONE_ID], bump=5)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(Exception):
                asyncio.run(self.hooks.async_pre_call_deployment_hook(
                    {"model": f"nvidia_nim/{self.GONE_ID}"}, None))

    def test_key_strips_a_provider_prefix_only_when_the_provider_is_absent(self):
        key = self.mod._cap_key
        self.assertEqual(key({"model": "nvidia_nim/deepseek-ai/x"}), "deepseek-ai/x")
        self.assertEqual(key({"model": "groq/openai/gpt-oss-120b"}), "openai/gpt-oss-120b")
        # Failure side: provider present, the model has no prefix -- must NOT lose "openai/".
        self.assertEqual(key({"model": "openai/gpt-oss-120b", "custom_llm_provider": "groq"}),
                         "openai/gpt-oss-120b")
        # An org segment that is not a provider is kept.
        self.assertEqual(key({"model": "stepfun-ai/step-5-preview"}), "stepfun-ai/step-5-preview")

    def test_listed_model_fails_over_without_an_upstream_call(self):
        self._write([self.GONE_ID], bump=5)
        with self.assertRaises(Exception) as ctx:
            self._pre(self.GONE_ID)
        self.assertIn("retired", str(ctx.exception))

    def test_a_retirement_recorded_while_the_proxy_runs_is_picked_up(self):
        out, _ = self._pre(self.GONE_ID)                  # not listed yet: goes upstream
        self.assertEqual(out["model"], f"nvidia_nim/{self.GONE_ID}")
        self._write([self.GONE_ID], bump=5)               # roster updated after proxy start
        with self.assertRaises(Exception):
            self._pre(self.GONE_ID)

    def test_skip_tells_the_user_to_relaunch_once(self):
        self._write([self.GONE_ID], bump=5)
        logs = []
        for _ in range(3):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(Exception):
                    asyncio.run(self.hooks.async_pre_call_deployment_hook(
                        {"model": f"nvidia_nim/{self.GONE_ID}", "custom_llm_provider": "nvidia_nim"}, None))
            logs.append(err.getvalue())
        self.assertIn("relaunch", logs[0])
        self.assertEqual(logs[1:], ["", ""], "one notice per model, not one per turn")

    def test_a_410_is_learned_even_when_the_list_lacks_the_model(self):
        import litellm
        exc = litellm.APIError(status_code=410, message=self.NIM_410, llm_provider="nvidia_nim",
                               model=self.GONE_ID)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            asyncio.run(self.hooks.async_log_failure_event(
                {"model": self.GONE_ID, "custom_llm_provider": "nvidia_nim", "exception": exc},
                None, None, None))
        self.assertIn("retired", err.getvalue())
        with self.assertRaises(Exception):
            self._pre(self.GONE_ID)
        out, _ = self._pre("z-ai/glm-5.3-flash")
        self.assertEqual(out["model"], "nvidia_nim/z-ai/glm-5.3-flash", "only the 410 model")

    def test_missing_or_corrupt_list_fails_open(self):
        self.broken.unlink()
        out, _ = self._pre(self.GONE_ID)
        self.assertEqual(out["model"], f"nvidia_nim/{self.GONE_ID}")
        self.broken.write_text("{not json")
        out, _ = self._pre(self.GONE_ID)
        self.assertEqual(out["model"], f"nvidia_nim/{self.GONE_ID}")

    def test_default_list_is_found_through_the_proxy_symlink(self):
        # Delivery route: remote-session.sh symlinks the hook into the proxy's tmp dir, so the
        # default must resolve THROUGH the link to the repo's config, not next to the link.
        link = self.tmp / "proxy" / "la_proxy_hooks.py"
        link.parent.mkdir()
        link.symlink_to(REPO / "bin" / "la_proxy_hooks.py")
        spec = importlib.util.spec_from_file_location("la_proxy_hooks_linked", link)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LA_BROKEN_MODELS_FILE", None)
            self.assertEqual(mod.retired_models_file(), REPO / "config" / "broken-nvidia-models.json")


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class NativeLaneTests(unittest.TestCase):
    """Fix 7: on the native Anthropic lane, Claude Code's PER-TURN effort (output_config on a
    mid-conversation role:system message, beta per-turn-control-2026-07-01) is folded into the
    request-level output_config. OpenRouter answered it on 2026-10-20 with 400 "Mid-conversation
    reasoning effort (configuration_update) is not supported on <model>" for most free models."""

    def setUp(self):
        self.mod = load_hooks()

    def test_per_turn_effort_moves_to_the_request(self):
        kwargs = {"model": "openai/openrouter/free", "output_config": {"effort": "high"},
                  "messages": [{"role": "user", "content": "a"},
                               {"role": "system", "content": "note", "output_config": {"effort": "max"}},
                               {"role": "user", "content": "b"}]}
        out = self.mod.fold_per_turn_effort(kwargs)
        self.assertEqual(out["output_config"], {"effort": "max"}, "the latest per-turn effort wins")
        self.assertTrue(all("output_config" not in m for m in out["messages"]))
        self.assertEqual(out["messages"][1]["content"], "note", "the message itself is kept")
        self.assertIn("output_config", kwargs["messages"][1], "the caller's dict is not mutated")

    def test_nothing_to_fold_returns_none(self):
        self.assertIsNone(self.mod.fold_per_turn_effort(
            {"messages": [{"role": "user", "content": "a"}], "output_config": {"effort": "low"}}))
        self.assertIsNone(self.mod.fold_per_turn_effort({"input": "x"}))


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class AccountErrorTests(unittest.TestCase):
    """Fix 6: an upstream refusal about the ACCOUNT (balance, credits, payment method, card) is
    answered as HTTP 402 naming the provider. Claude Code retries 429/5xx for minutes behind a
    countdown; a 402 it reports at once. Real texts from 2026-10-20 live proxy logs."""
    ACCOUNT = (
        ("zai", 429, "Insufficient balance or no resource package. Please recharge."),
        ("siliconflow", 402, "Sorry, your account balance is insufficient"),
        ("sambanova", 402, "A payment method is required. Add one at https://cloud.sambanova.ai/plans/billing to continue."),
        ("vercel", 403, "AI Gateway requires a valid credit card on file to service requests."),
        ("llm7", 402, '{"error":{"message":"Insufficient balance. Please top up your balance to continue."}}'),
        ("huggingface", 402, "You have exceeded your monthly included credits for Inference Providers."),
    )
    # Not the account, but equally not fixable by a retry within minutes: the session must say so
    # at once instead of counting down. Real texts from the 2026-10-20 live gate.
    HARD_LIMIT = (
        ("llm7", 429, "Daily token quota exceeded. Retry after 63713 seconds."),
        ("groq", 413, "Request too large for model `openai/gpt-oss-120b` in organization `org_x` service "
                      "tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 42136, please "
                      "reduce your message size and try again."),
    )
    NOT_ACCOUNT = ((429, "Rate limit exceeded"), (429, "Too Many Requests"), (400, "bad request"),
                   (503, "This model is currently experiencing high demand."))

    def test_hard_limits_are_recognised_and_named(self):
        for prov, status, text in self.HARD_LIMIT:
            with self.subTest(prov):
                self.assertTrue(self.mod.is_account_error(status, text))
                detail = self.mod.account_error_detail(prov, text, status)
                self.assertIn(prov, detail)
                self.assertNotIn("ACCOUNT", detail, "a quota/size limit is not a billing problem")
        self.assertIn("Claude Code's prompt alone", self.mod.account_error_detail("groq", self.HARD_LIMIT[1][2], 413))

    def setUp(self):
        self.mod = load_hooks()

    def test_account_refusals_are_recognised(self):
        for prov, status, text in self.ACCOUNT:
            with self.subTest(prov):
                self.assertTrue(self.mod.is_account_error(status, text))

    def test_rate_limits_are_not_account_errors(self):
        for status, text in self.NOT_ACCOUNT:
            with self.subTest(text):
                self.assertFalse(self.mod.is_account_error(status, text))

    def test_failure_hook_answers_402_naming_the_provider(self):
        import litellm
        hooks = self.mod.LAProxyHooks()
        hooks._bucket = None
        exc = litellm.RateLimitError(message="OpenAIException - " + self.ACCOUNT[0][2],
                                     llm_provider="openai", model="glm-5.3")
        os.environ["LA_REMOTE_PROVIDER"] = "zai"
        self.addCleanup(os.environ.pop, "LA_REMOTE_PROVIDER", None)
        with contextlib.redirect_stderr(io.StringIO()):
            out = asyncio.run(hooks.async_post_call_failure_hook({}, exc, None))
        self.assertEqual(out.status_code, 402)
        self.assertIn("zai", out.detail)
        self.assertIn("ACCOUNT", out.detail)
        self.assertIn("Insufficient balance", out.detail, "the provider's own words stay visible")
        # LiteLLM's /v1/messages endpoint ignores the returned HTTPException and replies from the
        # exception itself (measured on 1.105.1), so the exception must be rewritten in place.
        self.assertEqual(exc.status_code, 402)
        self.assertIn("ACCOUNT", exc.message)
        plain = litellm.RateLimitError(message="Rate limit exceeded", llm_provider="openai", model="m")
        self.assertIsNone(asyncio.run(hooks.async_post_call_failure_hook({}, plain, None)))


@unittest.skipUnless(HAVE_LITELLM, "run under the LiteLLM pipx interpreter")
class GrantPathTests(unittest.TestCase):
    """Task 3: the hook passes model= to aacquire (L1 — a chain-less proxy must wait out a
    per-model cooldown inside the queue) and maps RateLimitTimeout to a self-raised
    litellm.RateLimitError (L4 — a retriable 429 to the client, never a 500)."""

    def setUp(self):
        self.mod = load_hooks()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _hooks(self):
        from rate_limiter import SlidingWindowLimiter
        hooks = self.mod.LAProxyHooks()
        hooks._bucket = SlidingWindowLimiter(os.path.join(self.dir.name, "s"), rpm=40)
        return hooks

    def test_hook_passes_model_to_aacquire(self):
        hooks = self._hooks()
        seen = {}

        async def fake_aacquire(**kw):
            seen.update(kw)
            return 0.0

        hooks._bucket.aacquire = fake_aacquire
        asyncio.run(hooks.async_pre_call_deployment_hook({"model": "nvidia_nim/m1"}, None))
        self.assertEqual(seen.get("model"), "nvidia_nim/m1", seen)

    def test_rate_limit_timeout_maps_to_self_raised_rate_limit_error(self):
        import litellm
        from rate_limiter import RateLimitTimeout
        hooks = self._hooks()

        async def boom(**kw):
            raise RateLimitTimeout("no NVIDIA slot within 300s")

        hooks._bucket.aacquire = boom
        with self.assertRaises(litellm.RateLimitError) as cm:
            asyncio.run(hooks.async_pre_call_deployment_hook({"model": "nvidia_nim/m1"}, None))
        self.assertTrue(getattr(cm.exception, "la_self_raised", False),
                        "self-raised marker required so L8 never books it")


class TokensBookingTests(unittest.TestCase):
    """0.62.3 review fix: the proxy hook books tokens= at grant time and reconciles
    booked-vs-used on success (0.62.0 granted with tokens=0 — TPM accounting was
    limiter-API-only — and never refunded the estimate)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.mod = load_hooks()
        self.hooks = self.mod.LAProxyHooks()
        self.hooks._bucket = None

    def _fake_bucket(self):
        class FakeBucket:
            def __init__(self):
                self.acquire_kwargs = None
                self.success = None

            def model_cooling(self, model):
                return 0.0

            async def aacquire(self, **kw):
                self.acquire_kwargs = kw
                return 0.0

            def note_success(self, model=None, tokens_used=None, booked=0):
                self.success = (model, tokens_used, booked)
        return FakeBucket()

    def test_pre_call_books_estimated_tokens(self):
        b = self._fake_bucket()
        self.hooks._bucket = b
        kwargs = {"model": "nvidia_nim/acme/x", "max_tokens": 1000,
                  "messages": [{"role": "user", "content": "hi " * 100}]}
        asyncio.run(self.hooks.async_pre_call_deployment_hook(kwargs, None))
        self.assertIsNotNone(b.acquire_kwargs, "aacquire must be called")
        booked = b.acquire_kwargs.get("tokens", 0)
        self.assertGreaterEqual(booked, 1000, f"tokens= must cover max_tokens, got {booked}")
        self.assertEqual(kwargs.get("_la_booked_tokens"), booked,
                         "the booked amount must ride on kwargs for the success hook")

    def test_success_reconciles_booked_vs_used(self):
        b = self._fake_bucket()
        self.hooks._bucket = b
        kwargs = {"model": "nvidia_nim/acme/x", "_la_booked_tokens": 1250}
        resp = {"usage": {"prompt_tokens": 1000, "completion_tokens": 250}}
        asyncio.run(self.hooks.async_log_success_event(kwargs, resp, 0, 1))
        self.assertEqual(b.success, ("nvidia_nim/acme/x", 1250, 1250),
                         f"note_success must get tokens_used=1250, booked=1250, got {b.success}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
