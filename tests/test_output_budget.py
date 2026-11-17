#!/usr/bin/env python3
"""Adaptive output budget: a provider that refuses the requested max_tokens but names what it
WOULD accept gets exactly one retry at that ceiling (bin/output_budget.py + the dispatcher).

Measured 2026-11-17: the Mistral entry's max_tokens 128000 was inherited by its @openrouter
alternate; OpenRouter checks the CEILING against the credit balance before generating, so every
call 402'd ("You requested up to 128000 tokens, but can only afford 19039") while the same
request at max_tokens=1000 answered in 2.7 s. The log called it [malformed].

Usage: python3 -m pytest tests/test_output_budget.py
Environment: none required; the provider call is replaced, nothing is sent.
"""
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import output_budget  # noqa: E402

_spec = importlib.util.spec_from_file_location("rad", ROOT / "bin/remote-agent-dispatch.py")
assert _spec is not None and _spec.loader is not None
rad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rad)

OPENROUTER_402 = ('{"error":{"message":"This request requires more credits, or fewer max_tokens. '
                  'You requested up to 128000 tokens, but can only afford 19039. To increase, visit '
                  'https://openrouter.ai/settings/credits and upgrade to a paid account","code":402}}')
OPENAI_400 = ('{"error":{"message":"max_tokens is too large: 128000. This model supports at most '
              '32768 completion tokens, whereas you provided 128000.","type":"invalid_request_error"}}')
VLLM_400 = ('{"object":"error","message":"This model\'s maximum context length is 32768 tokens. '
            'However, you requested 140000 tokens (12000 in the messages, 128000 in the '
            'completion). Please reduce the length of the messages or completion."}')
ANTHROPIC_400 = ('{"type":"error","error":{"type":"invalid_request_error","message":"max_tokens: '
                 '128000 > 64000, which is the maximum allowed number of output tokens for '
                 'claude-x"}}')


class AllowedMaxTokensTests(unittest.TestCase):
    def test_openrouter_credit_limit_keeps_a_safety_margin(self):
        # a credit balance shrinks with use, so stay 5% under what it can afford
        self.assertEqual(output_budget.allowed_max_tokens(402, OPENROUTER_402, 128000), 18087)

    def test_openai_model_output_cap_is_used_exactly(self):
        self.assertEqual(output_budget.allowed_max_tokens(400, OPENAI_400, 128000), 32768)

    def test_vllm_context_arithmetic_leaves_room_for_the_prompt(self):
        # 32768 context - 12000 prompt tokens
        self.assertEqual(output_budget.allowed_max_tokens(400, VLLM_400, 128000), 20768)

    def test_anthropic_output_cap_is_used_exactly(self):
        self.assertEqual(output_budget.allowed_max_tokens(400, ANTHROPIC_400, 128000), 64000)

    def test_a_refusal_without_a_number_gives_no_ceiling(self):
        self.assertIsNone(output_budget.allowed_max_tokens(
            402, '{"error":{"message":"Insufficient credits"}}', 128000))

    def test_never_suggests_a_ceiling_at_or_above_the_request(self):
        # the limit is not the cause, so a retry would loop on the same refusal
        self.assertIsNone(output_budget.allowed_max_tokens(402, OPENROUTER_402, 18087))

    def test_a_ceiling_too_small_to_answer_is_refused(self):
        tiny = OPENROUTER_402.replace("19039", "100")
        self.assertIsNone(output_budget.allowed_max_tokens(402, tiny, 128000))

    def test_a_success_status_is_never_a_budget_refusal(self):
        self.assertIsNone(output_budget.allowed_max_tokens(200, OPENROUTER_402, 128000))


class DispatcherRetryTests(unittest.TestCase):
    def run_dispatch(self, results):
        calls = []

        def fake_stream(provider, body, *a, **k):
            calls.append(dict(body))
            r = {"model_effective": "m", **results[len(calls) - 1]}
            return rad.remote_http.Result(provider=provider.id, tier=provider.tier,
                                          model_requested=body["model"], **r)

        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}), \
                mock.patch.object(rad.remote_http, "stream_chat", side_effect=fake_stream), \
                mock.patch.object(rad, "_record_quota"), \
                mock.patch.object(sys, "argv", ["x", "--provider", "openrouter", "--model",
                                                "mistralai/mistral-large-4-0", "--prompt", "hi",
                                                "--max-tokens", "128000", "--outdir", td]), \
                mock.patch.object(sys, "stderr", new_callable=io.StringIO) as err:
            rc = rad.main()
        return rc, calls, err.getvalue()

    REFUSED = dict(ok=False, status=402, error_class="malformed", error_reason=OPENROUTER_402)
    ANSWERED = dict(ok=True, status=200, model_effective="m", output_chars=2, stream_complete=True)

    def test_budget_refusal_is_retried_once_at_the_allowed_ceiling(self):
        rc, calls, err = self.run_dispatch([self.REFUSED, self.ANSWERED])
        self.assertEqual(rc, 0)
        self.assertEqual([c["max_tokens"] for c in calls], [128000, 18087])

    def test_the_log_names_the_real_reason(self):
        _, _, err = self.run_dispatch([self.REFUSED, self.ANSWERED])
        self.assertIn("[output-budget]", err)
        self.assertIn("18087", err)

    def test_a_second_refusal_is_not_retried_again(self):
        rc, calls, _ = self.run_dispatch([self.REFUSED, self.REFUSED])
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(rc, 0)

    def test_an_unrelated_failure_is_not_retried(self):
        other = dict(ok=False, status=401, error_class="auth", error_reason='{"error":"bad key"}')
        rc, calls, _ = self.run_dispatch([other])
        self.assertEqual(len(calls), 1)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
