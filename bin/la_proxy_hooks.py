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
   (router retries included) first takes a slot in the file-backed sliding window in
   rate_limiter.py -- at most LA_NVIDIA_RPM in any rolling 60 s -- queueing instead of letting a
   429 reach Claude Code (whose retries would only add load). If NVIDIA still answers 429, the
   failure hook pauses EVERY proxy (Retry-After if sent, else LA_NVIDIA_429_COOLDOWN) and logs
   "NVIDIA 429 with N/40 requests in the last 60 s", the figure that locates the real limit.

4. Output ceiling, ANY provider. remote-session.sh sets CLAUDE_CODE_MAX_OUTPUT_TOKENS to Claude
   Code's own maximum (128000), and Claude Code sends that as max_tokens on every request. Most
   free providers accept it (NIM, Gemini, Cerebras, OpenRouter: measured 2026-10-08), but some
   reject anything above their own cap with HTTP 400 -- Groq: "`max_tokens` must be less than or
   equal to `16384`". The failure hook reads the cap out of that error, and every later request
   for that model is clamped to it; the proxy YAML's one BadRequest retry makes the failing turn
   itself succeed. So each model gets exactly its provider's ceiling, never a guessed lower one.

Usage:
  la_proxy_hooks.py --self-test   check the installed LiteLLM stream adapter handles mixed chunks
  la_proxy_hooks.py --help

Environment:
  LA_PROXY_HOOKS_RATE_LIMIT  0 disables the NVIDIA limiter (default 1)
  LA_NVIDIA_RPM, LA_NVIDIA_MAX_WAIT, LA_NVIDIA_429_COOLDOWN, LA_NVIDIA_THROTTLE_STATE
                             see rate_limiter.py --help
"""
from __future__ import annotations

import os
import re
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

# LiteLLM moves its Anthropic adapter between PRIVATE packages (experimental_pass_through ->
# pass_through in 1.105.1), so resolve the classes by NAME: known parents first, then a walk of
# litellm.llms.anthropic for a module with the same leaf. A relocation keeps working; only a
# renamed or removed class fails, which is a real signal rather than upgrade noise.
_ADAPTER_MODULES = {
    "AnthropicStreamWrapper": "adapters.streaming_iterator",
    "_CombinedChunkSplitter": "adapters.streaming_iterator",
    "LiteLLMAnthropicMessagesAdapter": "adapters.transformation",
}
_ADAPTER_PARENTS = ("pass_through", "experimental_pass_through")


def litellm_adapter(name: str):
    """Return LiteLLM's Anthropic adapter class `name`, wherever this LiteLLM keeps it."""
    import importlib
    rel = _ADAPTER_MODULES[name]
    candidates = [f"litellm.llms.anthropic.{parent}.{rel}" for parent in _ADAPTER_PARENTS]
    # Walk the FILES, not pkgutil: 1.105.1's pass_through/ has no __init__.py, and
    # pkgutil.walk_packages silently skips namespace packages.
    import litellm.llms.anthropic as root
    leaf = rel.rsplit(".", 1)[-1] + ".py"
    for base in map(Path, root.__path__):
        for path in sorted(base.rglob(leaf)):
            dotted = ".".join(path.relative_to(base).with_suffix("").parts)
            candidates.append(f"{root.__name__}.{dotted}")
    for modname in candidates:
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue
        if hasattr(mod, name):
            return getattr(mod, name)
    raise ImportError(f"this LiteLLM defines no {name} under litellm.llms.anthropic")


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
    AnthropicStreamWrapper = litellm_adapter("AnthropicStreamWrapper")
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


# ---- fix 4: learn each provider's output ceiling from its own 400 -------------------------

# The wording providers use when max_tokens is over their cap. Groq: "`max_tokens` must be less
# than or equal to `16384`"; OpenAI-style: "max_tokens is too large: 200000. This model supports
# at most 16384 completion tokens". Extend here when a new phrasing shows up in a proxy log.
_CAP_PATTERNS = (
    re.compile(r"max_tokens`?\s+must\s+be\s+less\s+than\s+or\s+equal\s+to\s+`?(\d+)", re.I),
    re.compile(r"supports\s+at\s+most\s+(\d+)\s+completion\s+tokens", re.I),
)
# Learned per model for this proxy's lifetime. A proxy serves one session, so this is per session.
_LEARNED_CAPS: dict[str, int] = {}


def _cap_key(kwargs: dict) -> str:
    """Same key from the pre-call side ("groq/qwen/x") and the failure side ("qwen/x")."""
    model = str(kwargs.get("model") or "")
    provider = str(kwargs.get("custom_llm_provider") or "")
    return model[len(provider) + 1:] if provider and model.startswith(provider + "/") else model


def output_cap_from_error(exc) -> int | None:
    """The provider's max_tokens ceiling if `exc` is a rejection of max_tokens, else None."""
    if exc is None or getattr(exc, "status_code", None) not in (400, None):
        return None
    text = str(exc)
    for pat in _CAP_PATTERNS:
        m = pat.search(text)
        if m and int(m.group(1)) > 0:
            return int(m.group(1))
    return None


def learn_output_cap(kwargs: dict) -> int | None:
    cap = output_cap_from_error((kwargs or {}).get("exception"))
    if cap is not None:
        _LEARNED_CAPS[_cap_key(kwargs)] = cap
    return cap


def clamp_max_tokens(kwargs: dict) -> dict | None:
    """kwargs with max_tokens lowered to the learned cap, or None when nothing changes."""
    cap = _LEARNED_CAPS.get(_cap_key(kwargs))
    asked = kwargs.get("max_tokens")
    if cap is None or not isinstance(asked, int) or asked <= cap:
        return None
    out = dict(kwargs)
    out["max_tokens"] = cap
    return out


# ---- fix 5: learn the request fields a model rejects from its own 400 ---------------------

# Same idiom as fix 4: the provider's 400 names what it refuses, the failure hook remembers it
# per model, and the router's one BadRequest retry plus every later request go out corrected.
# Real texts (2026-10-20 proxy logs): Groq "property 'prompt_cache_key' is unsupported"; LLM7
# "Model 'deepseek-v4-pro' does not support reasoning."; Z.ai glm-5.3-flash "This model always
# engages in thinking and cannot be disabled; please use low, high, or max".
_UNSUPPORTED_PROPERTY = re.compile(
    r"(?:property|parameter)\s+[`'\"]?([a-z_]+)[`'\"]?\s+is\s+(?:unsupported|not\s+supported)", re.I)
_NO_REASONING = re.compile(r"does\s+not\s+support\s+(?:reasoning|thinking)", re.I)
_ALWAYS_THINKS = re.compile(r"(?:always\s+engages\s+in\s+thinking|thinking\s+cannot\s+be\s+disabled|"
                            r"cannot\s+be\s+disabled.{0,40}(?:low|high))", re.I)
# Seeded: known before the first request, so that request never fails. LiteLLM adds
# prompt_cache_key because Groq's config CLAIMS support for it; Groq's API rejects it.
_PROVIDER_DROP = {"groq": {"prompt_cache_key"}}
_LEARNED_DROP: dict[str, set] = {}      # model -> request keys it rejected
_NO_REASONING_MODELS: set = set()
_ALWAYS_THINKING_MODELS: set = set()
_REASONING_KEYS = ("reasoning_effort", "thinking")


def learn_request_fix(kwargs: dict) -> str | None:
    """Remember what this model's 400 refused; return a log line, or None if nothing was learned."""
    exc = (kwargs or {}).get("exception")
    if exc is None or getattr(exc, "status_code", None) not in (400, 422, None):
        return None
    text, key = str(exc), _cap_key(kwargs)
    m = _UNSUPPORTED_PROPERTY.search(text)
    if m:
        _LEARNED_DROP.setdefault(key, set()).add(m.group(1))
        return f"{key} rejects '{m.group(1)}'; dropping it from its requests for this session"
    if _NO_REASONING.search(text):
        _NO_REASONING_MODELS.add(key)
        return f"{key} does not reason; sending it no reasoning_effort/thinking for this session"
    if _ALWAYS_THINKS.search(text):
        _ALWAYS_THINKING_MODELS.add(key)
        return f"{key} always thinks; a thinking-off turn is sent as reasoning_effort=low"
    return None


def apply_request_fixes(kwargs: dict) -> dict | None:
    """kwargs with the learned/seeded corrections applied, or None when nothing changes."""
    key = _cap_key(kwargs)
    provider = str(kwargs.get("custom_llm_provider") or "")
    drop = set(_PROVIDER_DROP.get(provider, ())) | _LEARNED_DROP.get(key, set())
    if key in _NO_REASONING_MODELS:
        drop.update(_REASONING_KEYS)
    out = {k: v for k, v in kwargs.items() if k not in drop}
    # Claude Code's thinking-disabled turns reach the provider as reasoning_effort="none".
    if key in _ALWAYS_THINKING_MODELS and out.get("reasoning_effort") in ("none", "disabled", None):
        if "reasoning_effort" in out or kwargs.get("thinking", {}) == {"type": "disabled"}:
            out["reasoning_effort"] = "low"
            out.pop("thinking", None)
    return None if out == kwargs else out


# ---- fix 6: account refusals reach the user at once, named, not as a retry countdown ------

# Wording providers use when the ACCOUNT (not the request, not the rate) is the problem. Real
# texts, 2026-10-20: zai 429 "Insufficient balance or no resource package. Please recharge.";
# siliconflow 402 "your account balance is insufficient"; sambanova 402 "A payment method is
# required"; vercel 403 "requires a valid credit card on file"; llm7 402 "Insufficient balance";
# huggingface 402 "exceeded your monthly included credits". Z.ai sends this as a 429, which
# Claude Code retries behind a countdown for minutes -- the "transient error" that never clears.
_ACCOUNT = re.compile(
    r"insufficient[_\s]+(?:balance|quota|credits?|funds)|balance\s+is\s+insufficient|"
    r"no\s+resource\s+package|please\s+(?:recharge|top\s+up)|top\s+up\s+your|"
    r"payment\s+method\s+is\s+required|credit\s+card|no\s+remaining\s+credits|"
    r"exceeded\s+your\s+monthly\s+included\s+credits|billing\s+(?:details|required)", re.I)


# Limits no retry within minutes can clear (2026-10-20 live gate): LLM7 429 "Daily token quota
# exceeded. Retry after 63713 seconds."; Groq 413 "Request too large ... tokens per minute (TPM):
# Limit 8000, Requested 42136" -- Claude Code's system prompt + tools alone exceed Groq's free TPM.
_DAILY_QUOTA = re.compile(r"daily\s+(?:token\s+)?(?:quota|limit)|quota\s+exceeded.{0,40}retry\s+after\s+\d{4,}", re.I)
_TOO_LARGE = re.compile(r"request\s+too\s+large|tokens\s+per\s+minute.{0,40}limit\s+\d+.{0,20}requested\s+\d+", re.I)


def is_account_error(status, text: str) -> bool:
    """True for refusals a retry cannot fix soon: account/billing, a daily quota, a too-large request."""
    text = text or ""
    if status in (402, 403, 429, 400, None) and _ACCOUNT.search(text):
        return True
    return status in (413, 429, 400, None) and bool(_DAILY_QUOTA.search(text) or _TOO_LARGE.search(text))


def account_error_detail(provider: str, text: str, status=None) -> str:
    who, said = provider or "the provider", text.strip()[:400]
    if _TOO_LARGE.search(text):
        return (f"{who} refused the request as too large for this model's free per-minute token "
                f"limit -- Claude Code's prompt alone (system prompt + tool definitions) is bigger "
                f"than that limit, so this model cannot run a Claude Code session on this tier. "
                f"Provider says: {said} -- pick another model or provider.")
    if _DAILY_QUOTA.search(text):
        return (f"{who} says today's quota for this model is used up. Provider says: {said} -- "
                f"it resets on the provider's schedule; pick another model or provider meanwhile.")
    return (f"{who} refused the request because of the provider ACCOUNT, not this proxy or the "
            f"model route. Provider says: {said} -- Free allowances may reset daily, so retry later; "
            f"otherwise check that account's billing or credits, or pick another provider.")


try:
    from litellm.integrations.custom_logger import CustomLogger
except Exception:                                      # allows --help/--self-test w/o litellm
    CustomLogger = object


def _retry_after_seconds(exc) -> float | None:
    """Retry-After from an upstream 429, if NVIDIA sent one (seconds or HTTP-date)."""
    headers = getattr(exc, "headers", None) or getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        from litellm.utils import _get_retry_after_from_exception_header
        value = _get_retry_after_from_exception_header(headers)
    except Exception:
        return None
    return float(value) if value and value > 0 else None


# The OpenAI SDK under LiteLLM's nvidia_nim path retries a 429 itself, twice, with a sub-second
# backoff: LiteLLM passes max_retries=2 to the client because nvidia_nim drops a caller's
# max_retries (measured on LiteLLM 1.102.1: 3 upstream hits per call with max_retries=0 set per
# request AND per deployment). Those attempts never reach async_pre_call_deployment_hook, so the
# window never saw them -- yet NVIDIA counted them. A failed 429 call therefore books this many
# extra slots. KNOWN GAP (re-measured 2026-10-22, LiteLLM 1.105.1, max_retries: 0 still ignored):
# a 429 the SDK retries and then WINS reaches no hook at all, so those attempts go unbooked --
# bounded by this number per throttle event; pinned by contract test 8b (expectedFailure).
SDK_HIDDEN_RETRIES = 2


def _is_429(exc) -> bool:
    return getattr(exc, "status_code", None) == 429 or type(exc).__name__ == "RateLimitError"


class LAProxyHooks(CustomLogger):
    def __init__(self):
        if CustomLogger is not object:
            super().__init__()
        self._bucket = None
        if os.environ.get("LA_PROXY_HOOKS_RATE_LIMIT", "1") != "0":
            from rate_limiter import get_limiter
            self._bucket = get_limiter()

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        changed = None
        clamped = clamp_max_tokens(kwargs)               # fix 4: every provider
        if clamped is not None:
            kwargs = changed = clamped
        fixed = apply_request_fixes(kwargs)              # fix 5: every provider
        if fixed is not None:
            kwargs = changed = fixed
        if not _is_nvidia(kwargs):
            # The rate limiter is NVIDIA's alone: no other provider has a known RPM ceiling.
            return changed
        if self._bucket is not None:
            model = str(kwargs.get("model") or "")
            cooling = self._bucket.model_cooling(model)
            if cooling > 0 and os.environ.get("LA_PROXY_FALLBACK_NAMES", ""):
                # 0.40.0 fast-fail: probed on LiteLLM 1.105.1, raising here makes the router
                # answer from the next fallback at once -- ZERO upstream calls to the cooling
                # model, and no 40 RPM slot spent (the fallback needs it). Only with a chain:
                # a chain-less proxy keeps queueing in aacquire exactly as before.
                import litellm
                print(f"la_proxy_hooks: {model} cooling {cooling:.0f}s after 429s "
                      f"-> failing over to a fallback at once", file=sys.stderr)
                raise litellm.RateLimitError(
                    message=f"model cooling {cooling:.0f}s after consecutive upstream 429s",
                    llm_provider="nvidia_nim", model=model)
            if getattr(call_type, "value", call_type) == "anthropic_messages":
                # 0.40.1: one /v1/messages request runs this hook TWICE with one call id --
                # here, as it arrives, and again as the translated acompletion that actually
                # goes upstream (probed on LiteLLM 1.105.1). NIM has no native /v1/messages,
                # so the inner call always follows: it books the slot, this one must not, or
                # 40 RPM is really 20. A router retry repeats BOTH layers, so it still books
                # exactly one -- which a dedupe on the call id would have swallowed.
                return normalize_nvidia_kwargs(kwargs)
            waited = await self._bucket.aacquire()
            if waited > 0:
                print(f"la_proxy_hooks: NVIDIA bucket queued this call {waited:.1f}s",
                      file=sys.stderr)
        return normalize_nvidia_kwargs(kwargs)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        # fix 4, any provider: a 400 naming the provider's max_tokens cap is remembered, so the
        # router's BadRequest retry and every later request go out clamped to it.
        cap = learn_output_cap(kwargs or {})
        if cap is not None:
            print(f"la_proxy_hooks: {_cap_key(kwargs)} rejects max_tokens above {cap}; "
                  f"clamping its requests to {cap} for this session", file=sys.stderr)
        lesson = learn_request_fix(kwargs or {}) if cap is None else None
        if lesson:
            print(f"la_proxy_hooks: {lesson}", file=sys.stderr)
        # Runs once per failed upstream ATTEMPT (router retries included). A 429 is CLASSIFIED
        # (0.40.0): a near-empty window is the provider throttling ONE model -- cool only that
        # model, other proxies run on; a window at least half full is our own RPM overrun --
        # pause every proxy on the machine. Either way, log how many requests the window held:
        # that number locates the real limit.
        if self._bucket is None or not _is_nvidia(kwargs or {}):
            return
        exc = (kwargs or {}).get("exception")
        if exc is None or not _is_429(exc):
            return
        model = str((kwargs or {}).get("model") or "")
        info = self._bucket.note_429(_retry_after_seconds(exc), hidden_attempts=SDK_HIDDEN_RETRIES,
                                     model=model)
        if info.get("kind") == "model_throttle":
            print(f"la_proxy_hooks: NVIDIA 429 with {info['in_window']}/{info['limit']} requests "
                  f"in the last 60 s -> model throttle: {info['model']} cools {info['pause']:.0f}s "
                  f"({info['consecutive_429s']}. in a row)", file=sys.stderr)
        else:
            print(f"la_proxy_hooks: NVIDIA 429 with {info['in_window']}/{info['limit']} requests "
                  f"in the last 60 s -> RPM overrun: all NVIDIA proxies pause {info['pause']:.0f}s",
                  file=sys.stderr)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """A COMPLETED request (0.40.0): clear that model's cooldown entry and reset the
        global backoff counter. These are the only resets -- an expired pause clears
        nothing, or a model throttled for hours retried at base cooldown forever."""
        if self._bucket is None or not _is_nvidia(kwargs or {}):
            return
        self._bucket.note_success(str((kwargs or {}).get("model") or ""))

    async def async_pre_request_hook(self, model, messages, kwargs):
        # fix 7, native Anthropic lane only (LA_PASSTHROUGH_MODELS set): fold per-turn effort into
        # the request's output_config. LiteLLM 1.105 passes `messages` separately and does not take
        # a replacement back, so the SAME list is edited in place.
        if not os.environ.get("LA_PASSTHROUGH_MODELS") or not isinstance(messages, list):
            return None
        folded = fold_per_turn_effort({"messages": messages, "output_config": kwargs.get("output_config")})
        if folded is None:
            return None
        messages[:] = folded["messages"]
        if folded.get("output_config"):
            kwargs["output_config"] = folded["output_config"]
        return kwargs

    async def async_post_call_failure_hook(self, request_data, original_exception,
                                           user_api_key_dict, traceback_str=None):
        # fix 6: what the CLIENT sees. An account refusal becomes a 402 that names the provider;
        # Claude Code shows a 402 at once instead of retrying a 429 behind a countdown.
        text = str(original_exception)
        if not is_account_error(getattr(original_exception, "status_code", None), text):
            return None
        from fastapi import HTTPException
        provider = os.environ.get("LA_REMOTE_PROVIDER", "")
        detail = account_error_detail(provider, text.split("Exception - ", 1)[-1].split("\nLiteLLM:")[0],
                                      getattr(original_exception, "status_code", None))
        print(f"la_proxy_hooks: {detail}", file=sys.stderr)
        # LiteLLM 1.105's /v1/messages endpoint awaits this hook but DISCARDS its return value and
        # builds the reply from the exception's own status_code/message/type -- so rewrite those
        # in place (Anthropic's 402 type is billing_error). The return still serves the endpoints
        # that honour it (chat/completions).
        for attr, value in (("status_code", 402), ("message", detail), ("type", "billing_error")):
            try:
                setattr(original_exception, attr, value)
            except Exception:
                pass
        return HTTPException(status_code=402, detail=detail)


# ---- fix 7: the native Anthropic lane keeps thinking + effort ------------------------------

def register_passthrough_models(ids: str) -> list:
    """Declare each id (comma list) reasoning- and effort-capable, so LiteLLM's /v1/messages
    passthrough FORWARDS thinking and output_config.effort instead of dropping them.

    Measured on 1.105.1 with an echo upstream: for an id missing from its model map LiteLLM logs
    "Dropping adaptive thinking/output_config.effort ... does not support extended thinking" and
    strips both, so a native lane would silently lose the effort the user chose. The lookup uses
    the BARE id as well as openai/<id>, so both are registered (one alone was measured not to work).
    """
    import litellm
    caps = {"litellm_provider": "openai", "mode": "chat", "supports_reasoning": True,
            "supports_output_config": True, "supports_adaptive_thinking": True,
            "supports_max_reasoning_effort": True, "supports_xhigh_reasoning_effort": True,
            "input_cost_per_token": 0, "output_cost_per_token": 0}
    done = []
    for mid in (i.strip() for i in (ids or "").split(",")):
        if mid:
            litellm.register_model({f"openai/{mid}": dict(caps), mid: dict(caps)})
            done.append(mid)
    return done


def fold_per_turn_effort(kwargs: dict) -> dict | None:
    """Move per-message output_config (Claude Code's per-turn effort on a mid-conversation
    role:system message) up to the request's own output_config; None when there is none.

    The per-turn form only Anthropic itself accepts: OpenRouter's /v1/messages refused it
    ("Mid-conversation reasoning effort (configuration_update) is not supported") for most free
    models on 2026-10-20, while the same effort at request level was honoured. The latest turn's
    setting wins, which is what the per-turn control means.
    """
    messages = kwargs.get("messages")
    if not isinstance(messages, list) or not any(
            isinstance(m, dict) and "output_config" in m for m in messages):
        return None
    out, latest, folded = dict(kwargs), None, []
    for m in messages:
        if isinstance(m, dict) and "output_config" in m:
            m = dict(m)
            latest = m.pop("output_config") or latest
        folded.append(m)
    out["messages"] = folded
    if isinstance(latest, dict):
        out["output_config"] = {**(kwargs.get("output_config") or {}), **latest}
    return out


# Loading this module (which LiteLLM does at proxy start) checks the upstream stream fix.
if CustomLogger is not object:
    check_stream_adapter()
    if os.environ.get("LA_PASSTHROUGH_MODELS"):
        register_passthrough_models(os.environ["LA_PASSTHROUGH_MODELS"])
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
