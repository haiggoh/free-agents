#!/usr/bin/env python3
"""la_proxy_hooks.py - LiteLLM callback that hardens the remote-session proxy for NVIDIA NIM.

Registered from the generated proxy YAML (`litellm_settings.callbacks: la_proxy_hooks.proxy_hooks`).
LiteLLM resolves that module RELATIVE TO THE CONFIG FILE, so remote-session.sh symlinks this file
next to proxy-<port>.yaml; the rate limiter is found through the symlink's resolved path.

Three fixes, each for a failure measured in a real proxy log (see the Bug Bash plan, W1):

1. "Content block is not a thinking block" -- with Nemotron thinking ON. LiteLLM 1.91's
   OpenAI->Anthropic stream adapter picks a chunk's BLOCK type with `content` winning over
   `reasoning_content`, but its DELTA type with `reasoning_content` winning over `content`.
   When NVIDIA packs the end of the reasoning and the first answer token into ONE chunk, the
   adapter opens a text block and streams a thinking_delta into it; Claude Code aborts the turn.
   Fix: split such a chunk in two (reasoning first, then the rest) before the adapter sees it.
   Thinking stays on; nothing is dropped.

2. HTTP 400 "Unsupported parameter(s): `stop_sequences` / `safeguards`". The Anthropic adapter
   forwards both keys untranslated and drop_params does not catch them. `stop_sequences` is
   TRANSLATED to OpenAI `stop` (which NIM supports) rather than dropped -- dropping it would let
   the Auto Mode classifier generate past `</severity>`, which its contract rejects.
   `safeguards` has no NIM equivalent and is dropped.

3. NVIDIA's 40 RPM free-tier limit across ALL proxies on the machine: every upstream NVIDIA call
   first takes a token from the file-backed bucket in rate_limiter.py, queueing instead of
   letting a 429 reach Claude Code (whose retries would only add load).

Usage:
  la_proxy_hooks.py --self-test   run the offline stream-split check against the installed LiteLLM
  la_proxy_hooks.py --help

Environment:
  LA_PROXY_HOOKS_RATE_LIMIT  0 disables the NVIDIA limiter (default 1)
  LA_NVIDIA_RPM, LA_NVIDIA_MAX_WAIT, LA_NVIDIA_THROTTLE_STATE  see rate_limiter.py --help
"""
from __future__ import annotations

import copy
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


# ---- fix 1: split mixed reasoning+content chunks -------------------------------------------

def _has(delta, name):
    return bool(getattr(delta, name, None))


def split_mixed_chunk(chunk):
    """Return [chunk], or [reasoning_chunk, rest_chunk] when one delta mixes both kinds."""
    choices = getattr(chunk, "choices", None)
    if not choices or len(choices) != 1:
        return [chunk]
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return [chunk]
    has_reasoning = _has(delta, "reasoning_content") or _has(delta, "thinking_blocks")
    has_other = _has(delta, "content") or _has(delta, "tool_calls")
    if not (has_reasoning and has_other):
        return [chunk]

    reasoning = copy.deepcopy(chunk)
    rd = reasoning.choices[0].delta
    rd.content = None
    if hasattr(rd, "tool_calls"):
        rd.tool_calls = None
    reasoning.choices[0].finish_reason = None          # the finish belongs to the LAST piece
    if hasattr(reasoning, "usage"):
        try:
            reasoning.usage = None
        except Exception:
            pass

    rest = copy.deepcopy(chunk)
    rest_d = rest.choices[0].delta
    if hasattr(rest_d, "reasoning_content"):
        rest_d.reasoning_content = None
    if hasattr(rest_d, "thinking_blocks"):
        rest_d.thinking_blocks = None
    return [reasoning, rest]


def install_stream_split_patch():
    """Wrap LiteLLM's _CombinedChunkSplitter._split so mixed chunks are split first.

    A runtime wrap from OUR repo, never an edit under ~/.local/pipx. It is guarded: if the
    private class or method is gone in a future LiteLLM, the patch is skipped LOUDLY and the
    proxy runs unpatched rather than crashing.
    """
    try:
        from litellm.llms.anthropic.experimental_pass_through.adapters import streaming_iterator as si
        splitter = si._CombinedChunkSplitter
        original = splitter.__dict__["_split"].__func__
    except Exception as exc:
        print(f"la_proxy_hooks: WARNING stream-split patch NOT installed ({exc}); "
              f"'not a thinking block' errors may return", file=sys.stderr)
        return False
    if getattr(original, "_la_patched", False):
        return True

    def _split(chunk):
        out = []
        for piece in split_mixed_chunk(chunk):
            out.extend(original(piece))
        return out

    _split._la_patched = True
    splitter._split = staticmethod(_split)
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


# Loading this module (which LiteLLM does at proxy start) installs the stream fix.
install_stream_split_patch()
proxy_hooks = LAProxyHooks() if CustomLogger is not object else None


# ---- CLI ------------------------------------------------------------------------------------

def _self_test() -> int:
    from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices
    from litellm.llms.anthropic.experimental_pass_through.adapters.streaming_iterator import (
        AnthropicStreamWrapper)

    def ch(content=None, reasoning=None, finish=None):
        return ModelResponseStream(choices=[StreamingChoices(
            index=0, delta=Delta(content=content, reasoning_content=reasoning),
            finish_reason=finish)])

    cases = {
        "mixed transition": [ch(reasoning="a"), ch(content="Hi", reasoning=" b"),
                             ch(content="!"), ch(finish="stop")],
        "mixed first": [ch(content="\n", reasoning="a"), ch(reasoning="b"),
                        ch(content="Hi"), ch(finish="stop")],
    }
    bad = 0
    for name, chunks in cases.items():
        open_types, errors, thinking = {}, [], ""
        for ev in AnthropicStreamWrapper(completion_stream=iter(chunks), model="m"):
            if ev.get("type") == "content_block_start":
                open_types[ev["index"]] = ev["content_block"]["type"]
            if ev.get("type") == "content_block_delta":
                dt, bt = ev["delta"]["type"], open_types.get(ev["index"])
                if (dt == "thinking_delta") != (bt == "thinking"):
                    errors.append(f"{dt} into {bt}")
                thinking += ev["delta"].get("thinking", "")
        print(f"{name:18s} -> {'OK' if not errors else errors}  thinking={thinking!r}")
        bad += bool(errors)
    return 1 if bad else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    if args[0] == "--self-test":
        sys.exit(_self_test())
    print(f"la_proxy_hooks: unknown argument {args[0]!r}; see --help", file=sys.stderr)
    sys.exit(2)
