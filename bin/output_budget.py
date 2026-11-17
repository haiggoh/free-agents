#!/usr/bin/env python3
"""Read the output-token ceiling a provider WOULD accept from its refusal.

Some providers refuse a request because its max_tokens is too high for them, and say in the
error what they would accept instead: OpenRouter checks the ceiling against the credit balance
("can only afford N"), OpenAI-style APIs cap completion tokens per model, vLLM-style servers
report the context arithmetic, Anthropic-style APIs name the output maximum. A refusal like that
is not a malformed request; the same request at the named ceiling succeeds. This module turns the
error text into that ceiling so a caller can retry ONCE (bin/remote-agent-dispatch.py does).

Adding a provider = adding one row to _PATTERNS. No per-provider cap is ever hardcoded: the number
always comes from the provider's own live answer.

Usage (library):  allowed_max_tokens(status, error_text, requested) -> int | None
Usage (CLI):      output_budget.py --status 402 --requested 128000 < error_body.txt
                  output_budget.py --help
Environment: none.
"""
import argparse
import re
import sys

# A ceiling below this cannot hold a useful answer (thinking models spend tokens before the text).
MIN_USEFUL_TOKENS = 256
# A credit balance shrinks with use, so stay under what it can afford right now.
CREDIT_SAFETY = 0.95

# (name, regex, how to turn the match into a ceiling, kind)
#   kind "credit": a balance-derived limit -> apply CREDIT_SAFETY
#   kind "hard":   a fixed model/server limit -> use exactly
_PATTERNS = [
    ("openrouter-credits",
     re.compile(r"requested up to (\d+) tokens, but can only afford (\d+)", re.I),
     lambda m: int(m.group(2)), "credit"),
    ("openai-completion-cap",
     re.compile(r"supports at most (\d+) completion tokens", re.I),
     lambda m: int(m.group(1)), "hard"),
    ("anthropic-output-cap",
     re.compile(r"max_tokens:\s*(\d+)\s*>\s*(\d+)", re.I),
     lambda m: int(m.group(2)), "hard"),
    ("context-arithmetic",
     re.compile(r"maximum context length is (\d+) tokens.*?\((\d+) in the messages", re.I | re.S),
     lambda m: int(m.group(1)) - int(m.group(2)), "hard"),
]


def explain(status, error_text, requested):
    """Return (ceiling, pattern_name) or (None, None). See allowed_max_tokens."""
    if 200 <= int(status or 0) < 300 or not error_text:
        return None, None
    for name, rx, extract, kind in _PATTERNS:
        m = rx.search(error_text)
        if not m:
            continue
        ceiling = extract(m)
        if kind == "credit":
            ceiling = int(ceiling * CREDIT_SAFETY)
        if ceiling < MIN_USEFUL_TOKENS or ceiling >= int(requested):
            # Not the cause (already under the limit) or too small to answer: a retry would
            # only repeat the refusal or return nothing.
            return None, None
        return ceiling, name
    return None, None


def allowed_max_tokens(status, error_text, requested):
    """The max_tokens a retry should use, or None when the refusal names no usable ceiling.

    Never returns a value at or above `requested` (so a caller cannot loop on it) and never one
    below MIN_USEFUL_TOKENS.
    """
    return explain(status, error_text, requested)[0]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Print the max_tokens a provider would accept, read from its error body "
                    "on stdin; exit 1 when the error names no usable ceiling.")
    ap.add_argument("--status", type=int, required=True, help="the HTTP status of the refusal")
    ap.add_argument("--requested", type=int, required=True, help="the max_tokens that was sent")
    a = ap.parse_args(argv)
    ceiling, name = explain(a.status, sys.stdin.read(), a.requested)
    if ceiling is None:
        print("no usable output-token ceiling in that error", file=sys.stderr)
        return 1
    print("%d\t%s" % (ceiling, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
