#!/usr/bin/env python3
"""la_proxy_hooks.py - LiteLLM callback that hardens the remote-session proxy for NVIDIA NIM.

Registered from the generated proxy YAML (`litellm_settings.callbacks: la_proxy_hooks.proxy_hooks`).
LiteLLM resolves that module RELATIVE TO THE CONFIG FILE, so remote-session.sh symlinks this file
next to proxy-<port>.yaml; the rate limiter is found through the symlink's resolved path.

Two fixes plus one upstream check, each for a failure measured in a real proxy log (see the
Bug Bash plan, W1):

1. "Content block is not a thinking block" -- with Nemotron thinking ON. LiteLLM 1.91's
   OpenAI->Anthropic stream adapter mistyped a chunk carrying both reasoning and content
   (text block opened, thinking_delta streamed into it). 0.19.10 patched this at runtime;
   LiteLLM 1.102.1 fixed it upstream, so the patch is RETIRED. The module now only PROBES the
   installed adapter at load and warns loudly if it still has the bug (upgrade LiteLLM).

2. HTTP 400 "Unsupported parameter(s): `safeguards`" (and `stop_sequences` before 1.102.1). The
   Anthropic adapter forwards `safeguards` untranslated and drop_params does not catch it, so it
   is dropped for NIM. `stop_sequences` is translated to OpenAI `stop` if it still arrives (an
   older LiteLLM) -- dropping it would let the Auto Mode classifier generate past `</severity>`,
   which its contract rejects. 1.102.1 does that translation itself.

3. NVIDIA's 40 RPM free-tier limit across ALL proxies on the machine: every upstream NVIDIA call
   first takes a token from the file-backed bucket in rate_limiter.py, queueing instead of
   letting a 429 reach Claude Code (whose retries would only add load).

Usage:
  la_proxy_hooks.py --self-test   check the installed LiteLLM stream adapter handles mixed chunks
  la_proxy_hooks.py --help

Environment:
  LA_PROXY_HOOKS_RATE_LIMIT  0 disables the NVIDIA limiter (default 1)
  LA_NVIDIA_RPM, LA_NVIDIA_MAX_WAIT, LA_NVIDIA_THROTTLE_STATE  see rate_limiter.py --help
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BIN = Path(__file__).resolve().parent
if str(_BIN) not in sys.path:
    sys.path.insert(0, str(_BIN))

NVIDIA_PREFIX = "nvidia_nim/"
# Keys the Anthropic adapter forwards verbatim that NIM rejects with HTTP 400. Extend here when a
# new Claude Code request field shows up as "Unsupported parameter(s)" in a proxy log.
NVIDIA_DROP_KEYS = ("safeguards",)


# ---- upstream check: the mixed-chunk stream bug (fixed in LiteLLM 1.102.1) -----------------

MIN_LITELLM = "1.102.1"


def _probe_chunks():
    from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

    def ch(content=None, reasoning=None, finish=None):
        return ModelResponseStream(choices=[StreamingChoices(
            index=0, delta=Delta(content=content, reasoning_content=reasoning),
            finish_reason=finish)])

    return {
        "mixed transition": [ch(reasoning="a"), ch(content="Hi", reasoning=" b"),
                             ch(content="!"), ch(finish="stop")],
        "mixed first": [ch(content="\n", reasoning="a"), ch(reasoning="b"),
                        ch(content="Hi"), ch(finish="stop")],
    }


def stream_mismatches(chunks):
    """Drive LiteLLM's real Anthropic stream adapter; return (errors, text, thinking)."""
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


def check_stream_adapter() -> bool:
    """True if the installed adapter handles mixed chunks; warn LOUDLY (never crash) if not.

    Replaces the runtime split patch of 0.19.10: upstream fixed the bug, so we no longer wrap
    a private LiteLLM class -- but an old install must not regress silently either.
    """
    try:
        bad = [name for name, chunks in _probe_chunks().items() if stream_mismatches(chunks)[0]]
    except Exception as exc:
        print(f"la_proxy_hooks: WARNING could not probe the stream adapter ({exc})",
              file=sys.stderr)
        return False
    if bad:
        print(f"la_proxy_hooks: WARNING installed LiteLLM still has the mixed-chunk bug "
              f"({', '.join(bad)}); expect 'Content block is not a thinking block' on Nemotron. "
              f"Upgrade: pipx upgrade litellm  (>= {MIN_LITELLM})", file=sys.stderr)
        return False
    return True


# ---- fixes 2 + 3: the pre-call deployment hook --------------------------------------------

def normalize_nvidia_kwargs(kwargs: dict) -> dict:
    """Translate/drop request keys NIM rejects. Returns the (possibly new) kwargs dict."""
    out = dict(kwargs)
    stops = out.pop("stop_sequences", None)
    if stops and not out.get("stop"):
        out["stop"] = list(stops)[:4]                  # OpenAI-style APIs cap stop at 4
    for key in NVIDIA_DROP_KEYS:
        out.pop(key, None)
    return out


def _is_nvidia(kwargs: dict) -> bool:
    model = str(kwargs.get("model") or "")
    provider = str(kwargs.get("custom_llm_provider") or "")
    return model.startswith(NVIDIA_PREFIX) or provider == "nvidia_nim"


try:
    from litellm.integrations.custom_logger import CustomLogger
except Exception:                                      # allows --help/--self-test w/o litellm
    CustomLogger = object


class LAProxyHooks(CustomLogger):
    def __init__(self):
        if CustomLogger is not object:
            super().__init__()
        self._bucket = None
        if os.environ.get("LA_PROXY_HOOKS_RATE_LIMIT", "1") != "0":
            from rate_limiter import FileTokenBucket
            self._bucket = FileTokenBucket()

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        if not _is_nvidia(kwargs):
            return None
        if self._bucket is not None:
            waited = await self._bucket.aacquire()
            if waited > 0:
                print(f"la_proxy_hooks: NVIDIA bucket queued this call {waited:.1f}s",
                      file=sys.stderr)
        return normalize_nvidia_kwargs(kwargs)


# Loading this module (which LiteLLM does at proxy start) checks the upstream stream fix.
if CustomLogger is not object:
    check_stream_adapter()
proxy_hooks = LAProxyHooks() if CustomLogger is not object else None


# ---- CLI ------------------------------------------------------------------------------------

def _self_test() -> int:
    ok = True
    for name, chunks in _probe_chunks().items():
        errors, _, thinking = stream_mismatches(chunks)
        print(f"{name:18s} -> {'OK' if not errors else errors}  thinking={thinking!r}")
        ok &= not errors
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    if args[0] == "--self-test":
        sys.exit(_self_test())
    print(f"la_proxy_hooks: unknown argument {args[0]!r}; see --help", file=sys.stderr)
    sys.exit(2)
