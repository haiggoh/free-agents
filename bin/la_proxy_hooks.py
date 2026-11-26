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

5. LiteLLM #53340 bridge (0.62.0, provider-buckets): the /v1/messages bridge on LiteLLM
   1.110.2 turns a mid-flight upstream stream death into a 500 (or a silent empty 200) and
   cannot fall back. install_bridge_shim() guards the funnel AT RUNTIME -- None stream /
   None response raise retriable APIConnectionErrors, a zero-chunk stream errors instead of
   synthesizing an empty message -- and self-retires once an upgraded LiteLLM ships the
   guard. A fallback chain that ENDED on a rate limit reaches the client as 429 +
   Retry-After (streaming-iterator hook), and our own self-raised refusals are never booked
   as upstream 429s (defect L8). See docs/measurements/2026-11-22-bridge-experiment/.

Usage:
  la_proxy_hooks.py --self-test   check the installed LiteLLM stream adapter handles mixed chunks
  la_proxy_hooks.py --help

Environment:
  LA_PROXY_HOOKS_RATE_LIMIT  0 disables the NVIDIA limiter (default 1)
  LA_NVIDIA_RPM, LA_NVIDIA_MAX_WAIT, LA_NVIDIA_429_COOLDOWN, LA_NVIDIA_THROTTLE_STATE
                             see rate_limiter.py --help
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_BIN = Path(__file__).resolve().parent
if str(_BIN) not in sys.path:
    sys.path.insert(0, str(_BIN))

NVIDIA_PREFIX = "nvidia_nim/"
# Keys the Anthropic adapter forwards verbatim that NIM rejects with HTTP 400. Extend here when a
# new Claude Code request field shows up as "Unsupported parameter(s)" in a proxy log.
NVIDIA_DROP_KEYS = ("safeguards",)

# Exceptions the hooks raise THEMSELVES (fast-fail, bucket timeouts, the 429 mapping) carry
# this marker: async_log_failure_event must never book them as upstream 429s (defect L8 --
# measured 2026-11-22: a self-raised fast-fail was booked against the PRIMARY with 2 phantom
# hidden-retry slots, self-amplifying every cooldown), and the stream-iterator hook uses the
# recorded call id to answer the client 429 + Retry-After instead of a 500.
SELF_RAISED_ATTR = "la_self_raised"


# ---- upstream check: the mixed-chunk stream bug (fixed in LiteLLM 1.102.1) -----------------

MIN_LITELLM = "1.102.1"

# LiteLLM moves its Anthropic adapter between PRIVATE packages (experimental_pass_through ->
# pass_through in 1.105.1), so resolve the classes by NAME: known parents first, then a walk of
# litellm.llms.anthropic for a module with the same leaf. A relocation keeps working; only a
# renamed or removed class fails, which is a real signal rather than upgrade noise.
_ADAPTER_MODULES = {
    "AnthropicStreamWrapper": "adapters.streaming_iterator",
    "_CombinedChunkSplitter": "adapters.streaming_iterator",
    "AnthropicAdapter": "adapters.transformation",
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


def _provider_of(kwargs: dict) -> str:
    """The registry provider id for a deployment (0.62.0 C2): the litellm prefix maps to it
    (nvidia_nim -> nvidia); anything else is the prefix itself (groq/, mistral/, ...)."""
    model = str(kwargs.get("model") or "")
    provider = str(kwargs.get("custom_llm_provider") or "")
    prefix = model.split("/", 1)[0] if "/" in model else ""
    if prefix == "nvidia_nim" or provider == "nvidia_nim":
        return "nvidia"
    return prefix or provider


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


_FALLBACK_PROVIDERS = frozenset({"nvidia_nim", "groq", "openai", "anthropic", "gemini", "openrouter",
                                 "mistral", "deepseek", "huggingface", "together_ai", "cerebras", "sambanova"})


def _provider_names() -> frozenset:
    try:
        import litellm
        return frozenset(str(getattr(p, "value", p)) for p in litellm.provider_list) | _FALLBACK_PROVIDERS
    except Exception:
        return _FALLBACK_PROVIDERS


def _cap_key(kwargs: dict) -> str:
    """Same key from the pre-call side ("groq/qwen/x") and the failure side ("qwen/x").

    0.62.1: through LiteLLM's HTTP layer the deployment hook gets custom_llm_provider=None with
    the prefix still on the model ('nvidia_nim/<id>', measured 2026-11-23), so every learned
    lesson keyed '<id>' on the failure side missed. With no provider, a leading segment that
    names a LiteLLM provider is stripped; with one, only that provider's prefix is."""
    model = str(kwargs.get("model") or "")
    provider = str(kwargs.get("custom_llm_provider") or "")
    if provider:
        return model[len(provider) + 1:] if model.startswith(provider + "/") else model
    head, sep, rest = model.partition("/")
    return rest if sep and rest and head in _provider_names() else model


def _request_kind(kwargs: dict) -> str | None:
    """0.63.0: per-request kind for the fast lane. A subagent/agent caller marks its request
    with the X-LA-Agent header (set by agent-driven launchers and dispatchers); otherwise
    None -> the limiter falls back to the process env kind (dispatchers set dispatch)."""
    headers = kwargs.get("headers") or {}
    try:
        items = headers.items() if hasattr(headers, "items") else []
        for k, v in items:
            if str(k).lower() == "x-la-agent" and str(v).strip().lower() not in ("", "0", "false"):
                return "agent"
    except Exception:
        pass
    return None


def _estimate_tokens(kwargs: dict) -> int:
    """0.62.3 review fix: tokens booked in the quota sidecar at grant time — the completion
    cap plus a flat prompt estimate (chars//4). The REAL usage reconciles on success via
    note_success(tokens_used, booked) -> quota.refund(booked - used)."""
    try:
        mx = int(kwargs.get("max_tokens") or 0)
    except (TypeError, ValueError):
        mx = 0
    prompt_chars = len(str(kwargs.get("messages") or ""))
    return max(0, mx) + prompt_chars // 4


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
# 0.62.1: a text-only model refusing images. Real NIM text (proxy-4143.log, 2026-11-23): "Received
# multimodal data but multimodal processing is not enabled". Claude Code resends the whole history
# every turn, so ONE screenshot made every text-only fallback fail every turn, forever.
_NO_IMAGES = re.compile(r"multimodal\s+(?:data|processing|input)|does\s+not\s+support\s+(?:image|vision)|"
                        r"image\s+input\s+is\s+not\s+supported", re.I)
_TEXT_ONLY_MODELS: set = set()
IMAGE_PLACEHOLDER = "[image omitted: this fallback model cannot read images]"
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
    if _NO_IMAGES.search(text):
        _TEXT_ONLY_MODELS.add(key)
        return f"{key} is text-only; its requests go out with images replaced by a note for this session"
    return None


def strip_images(node):
    """A copy of `node` with every image block (Anthropic `image`, OpenAI `image_url`) replaced
    by a text note, recursing into nested content such as tool_result. Never edits `node`: the
    router shares one request between the primary and every fallback."""
    if isinstance(node, list):
        return [strip_images(item) for item in node]
    if not isinstance(node, dict):
        return node
    if node.get("type") in ("image", "image_url", "input_image"):
        return {"type": "text", "text": IMAGE_PLACEHOLDER}
    return {k: strip_images(v) if isinstance(v, (list, dict)) else v for k, v in node.items()}


def apply_request_fixes(kwargs: dict) -> dict | None:
    """kwargs with the learned/seeded corrections applied, or None when nothing changes."""
    key = _cap_key(kwargs)
    provider = str(kwargs.get("custom_llm_provider") or "")
    drop = set(_PROVIDER_DROP.get(provider, ())) | _LEARNED_DROP.get(key, set())
    if key in _NO_REASONING_MODELS:
        drop.update(_REASONING_KEYS)
    out = {k: v for k, v in kwargs.items() if k not in drop}
    if key in _TEXT_ONLY_MODELS and isinstance(out.get("messages"), list):
        out["messages"] = strip_images(out["messages"])
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


def _is_offline(text: str) -> bool:
    """0.50.1: a connect-level failure = the network is down (wifi cut), not a model error."""
    low = text.lower()
    return any(t in low for t in ("failed to connect", "connection refused",
                                  "network is unreachable", "no route to host",
                                  "name or service not known", "enotfound", "econnrefused",
                                  "ehostunreach", "enetunreach", "api connection error"))


OFFLINE_DETAIL = ("network unreachable from here (wifi off?) — the provider cannot be "
                  "reached at all right now, so the model is fine; this session will retry "
                  "when you are back online")


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


def _chain_has_self_raised(exc) -> bool:
    """True if `exc` or anything in its __cause__/__context__ chain is self-raised."""
    seen: set[int] = set()
    pending = [exc]
    while pending:
        e = pending.pop(0)
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if getattr(e, SELF_RAISED_ATTR, False):
            return True
        pending.extend(c for c in (getattr(e, "__cause__", None),
                                   getattr(e, "__context__", None)) if c is not None)
    return False


# ---- C4: LiteLLM #53340 bridge shim + client-facing error mapping (0.62.0) -----------------
# Measured (docs/measurements/2026-11-22-bridge-experiment/): on 1.110.2 a fallback chain
# ending on a rate limit reaches the client as HTTP 500 naming the PRIMARY's stream error;
# an upstream stream that dies before ANY chunk is a silent empty 200. This section guards
# the bridge funnel AT RUNTIME (never the pipx venv) and maps the client-visible error.

BRIDGE_SHIM_STATE = ""   # "" until the first install; then installed | upstream-guard-present | adapter-absent

_CALL_429_RECORDS: dict[str, dict] = {}   # litellm_call_id -> {"retry_after": seconds, "at": t}
_RECORD_TTL = 600.0

# 0.63.4: the grant-time booking's RECEIPT, by litellm_call_id, for the success hook —
# settle(receipt, used) reconciles exactly that reservation. NEVER in the request kwargs:
# LiteLLM forwards unknown kwargs upstream and NVIDIA NIM 400s every request on them
# (0.62.3 put `_la_booked_tokens` there). A receipt lives exactly as long as its request:
# the failure callback pops it (the sidecar booking STAYS — the provider may have counted
# the tokens), and the prune below only drops entries no live request can still own.
from quota_ledger import Receipt

# The longest a live request can hold a booking: the patient-tier queue cap plus the stream
# silence timeout, doubled as the safety factor for callbacks that never fire. Both derive
# from their real sources — the limiter's stretched-wait cap (ONE source of truth, shared
# with stretched_wait()) and the LA_STREAM_TIMEOUT default remote-session.sh bakes into the
# proxy YAML. An OLD rate_limiter running next to this hook (mixed fleet) has neither
# constant: fall back to the documented figures rather than refuse to start — the bound
# caps memory, it is never a correctness tool.
try:
    from rate_limiter import STRETCHED_WAIT_CAP as MAX_WAIT_S
except ImportError:                                        # mixed fleet, pre-receipt limiter
    MAX_WAIT_S = 1800.0
    print("la_proxy_hooks: rate_limiter has no STRETCHED_WAIT_CAP (pre-receipt limiter?); "
          "using 1800 s for the booking bound", file=sys.stderr)
_raw_stream_timeout = os.environ.get("LA_STREAM_TIMEOUT")
try:
    STREAM_TIMEOUT_S = float(_raw_stream_timeout or 300.0)   # "0" (disabled) -> 0: bound 2x queue
except ValueError:
    STREAM_TIMEOUT_S = 300.0
    print(f"la_proxy_hooks: LA_STREAM_TIMEOUT='{_raw_stream_timeout}' is not a number; "
          "using 300 s for the booking bound", file=sys.stderr)
_RECORD_BOUND = 2.0 * (float(MAX_WAIT_S) + STREAM_TIMEOUT_S)


def _repo_version() -> str:
    """The repo's VERSION file (one line), or 'receipt' when it cannot be read."""
    try:
        return (_BIN.parent / "VERSION").read_text().strip() or "receipt"
    except OSError:
        return "receipt"


def _disk_receipts_api(path: Path) -> int:
    """The RECEIPTS_API marker in the ON-DISK source (parsed, never executed — the
    file may be a different version than the loaded module). No marker -> 0."""
    try:
        m = re.search(r"^RECEIPTS_API\s*=\s*(\d+)", path.read_text(), re.M)
        return int(m.group(1)) if m else 0
    except OSError:
        return 0


def _warn_pre_receipt_limiter(loaded: dict | None = None, version: str | None = None) -> None:
    """ONE launch-time line per stale module: this process LOADED pre-receipt limiter or
    ledger code while the on-disk source has the receipts API (the proxy started before
    an update). The loaded code's wholesale sidecar rewrites drop receipt ids, so exact
    quota receipts stay wrong until the proxy is restarted — say so, and NEVER restart
    anything here (a restart would kill the sessions on that proxy)."""
    levels = loaded if loaded is not None else {
        name: int(getattr(sys.modules.get(name), "RECEIPTS_API", 0) or 0)
        for name in ("quota_ledger", "rate_limiter")}
    for name, have in levels.items():
        disk = _disk_receipts_api(_BIN / f"{name}.py")
        if disk > have:
            v = version or _repo_version()
            print(f"free-agents: this proxy runs pre-{v} limiter code — restart it to "
                  f"get exact quota receipts ({name}: loaded {have}, on disk {disk})",
                  file=sys.stderr)


_warn_pre_receipt_limiter()   # launch-time check; silent when loaded == on-disk

_CALL_BOOKED: dict[str, Receipt] = {}
# 0.63.4: the provider the pre-call booked under, by call id. The SUCCESS event reports
# the RESOLVED model ('openai/...' for a groq/openai/... deployment), so re-deriving the
# provider there finds no limits entry and the settle never runs (measured 2026-11-25).
_CALL_PROVIDER: dict[str, tuple] = {}   # litellm_call_id -> (provider, at)


def _now() -> float:
    return time.time()


def _book_call(kwargs: dict, receipt) -> None:
    now = _now()
    for k in [k for k, r in _CALL_BOOKED.items() if now - r.at > _RECORD_BOUND]:
        _CALL_BOOKED.pop(k, None)
    # prune the provider notes by their OWN timestamps: a failed call pops its
    # _CALL_BOOKED entry early, so its _CALL_PROVIDER entry must not leak
    for k in [k for k, v in _CALL_PROVIDER.items() if now - float(v[1]) > _RECORD_BOUND]:
        _CALL_PROVIDER.pop(k, None)
    call_id = str((kwargs or {}).get("litellm_call_id") or "")
    if call_id and receipt is not None:
        _CALL_BOOKED[call_id] = receipt


def _note_call_provider(kwargs: dict, provider: str) -> None:
    call_id = str((kwargs or {}).get("litellm_call_id") or "")
    if call_id and provider:
        _CALL_PROVIDER[call_id] = (provider, _now())


def _take_call_provider(kwargs: dict):
    v = _CALL_PROVIDER.pop(str((kwargs or {}).get("litellm_call_id") or ""), None)
    return (v[0] if v else None) or None


def _take_booked(kwargs: dict):
    call_id = str((kwargs or {}).get("litellm_call_id") or "")
    return _CALL_BOOKED.pop(call_id, None)


def _record_call_rate_limited(call_id: str, retry_after: float | None) -> None:
    """Remember that THIS call's chain ended on a rate limit; the stream-iterator hook
    turns it into a client 429 + Retry-After. Entries expire (call ids are never reused)."""
    import time
    now = time.time()
    for k in [k for k, v in _CALL_429_RECORDS.items()
              if now - float(v.get("at", 0)) > _RECORD_TTL]:
        _CALL_429_RECORDS.pop(k, None)
    if call_id:
        _CALL_429_RECORDS[call_id] = {"retry_after": float(retry_after or 0.0), "at": now}


def _remaining_call_rate_limit(call_id: str) -> float | None:
    """Seconds left of the cooldown recorded for this call id (pops it), else None."""
    import time
    rec = _CALL_429_RECORDS.pop(call_id or "", None)
    if not rec or not rec.get("retry_after"):
        return None
    return max(1.0, float(rec["retry_after"]) - (time.time() - float(rec["at"])))


def _retry_after_for_chain(exc) -> float | None:
    """Shortest remaining cooldown named by the exception chain -- a Retry-After header or
    a la_cooldown_seconds marker on any link -- else None. The dispatch tool reuses this."""
    seen: set[int] = set()
    best: float | None = None
    pending = [exc]
    while pending:
        e = pending.pop(0)
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        pending.extend(c for c in (getattr(e, "__cause__", None),
                                   getattr(e, "__context__", None)) if c is not None)
        secs = _retry_after_seconds(e)
        mark = getattr(e, "la_cooldown_seconds", None)
        if mark:
            try:
                mark = float(mark)
            except (TypeError, ValueError):
                mark = None
        if mark and mark > 0:
            secs = mark if not secs else min(secs, mark)
        if secs and secs > 0:
            best = secs if best is None else min(best, secs)
    return best


class _ZeroChunkGuard:
    """Wraps a RAW upstream chunk stream and raises (instead of ending quietly) when the
    stream yielded ZERO chunks. Measured die0 shape: the adapter otherwise synthesizes an
    empty-but-complete Anthropic message (message_start..message_stop, no text, no error)
    and the client gets a silent empty 200. One or more chunks pass through untouched."""

    def __init__(self, inner, on_empty):
        self._inner = inner
        self._on_empty = on_empty
        self._n = 0

    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = next(self._inner)
        except StopIteration:
            if self._n == 0:
                raise self._on_empty() from None
            raise
        self._n += 1
        return chunk

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self._inner.__anext__()
        except StopAsyncIteration:
            if self._n == 0:
                raise self._on_empty() from None
            raise
        self._n += 1
        return chunk


def install_bridge_shim() -> str:
    """Guard LiteLLM's /v1/messages bridge against the #53340 shapes AT RUNTIME (never the
    pipx venv), mirroring upstream PR #53341: a None completion_stream -- and the non-stream
    sibling, a None response/choices -- raises APIConnectionError (retriable, fallback-
    eligible) instead of surfacing as a 500 AttributeError. Plus the actually-measured
    harm: a raw stream with ZERO chunks surfaces as a retriable error, not a silent empty
    200. Self-retiring: an installed LiteLLM that already carries the guard is detected by
    source inspection and left untouched. Returns exactly one of "installed" |
    "upstream-guard-present" | "adapter-absent"."""
    global BRIDGE_SHIM_STATE
    if BRIDGE_SHIM_STATE:
        return BRIDGE_SHIM_STATE
    import functools
    import inspect
    try:
        import litellm
        stream_cls = litellm_adapter("AnthropicAdapter")
        messages_cls = litellm_adapter("LiteLLMAnthropicMessagesAdapter")
    except Exception as exc:
        print(f"la_proxy_hooks: bridge shim: adapter absent ({exc}) -- no guard installed",
              file=sys.stderr)
        BRIDGE_SHIM_STATE = "adapter-absent"
        return BRIDGE_SHIM_STATE

    def _api_error(model, why):
        e = litellm.APIConnectionError(
            message=f"upstream stream {why} (LiteLLM #53340 shape); retriable",
            llm_provider="anthropic", model=str(model or ""))
        e.status_code = 529
        e.type = "overloaded_error"
        return e

    stream_fn = getattr(stream_cls, "translate_completion_output_params_streaming", None)
    if stream_fn is None:
        print("la_proxy_hooks: bridge shim: streaming funnel missing -- no guard installed",
              file=sys.stderr)
        BRIDGE_SHIM_STATE = "adapter-absent"
        return BRIDGE_SHIM_STATE
    try:
        upstream_guarded = "completion_stream is None" in inspect.getsource(stream_fn)
    except OSError:
        upstream_guarded = False
    if upstream_guarded:
        print("la_proxy_hooks: bridge shim: upstream guard present -- shim off", file=sys.stderr)
        BRIDGE_SHIM_STATE = "upstream-guard-present"
        return BRIDGE_SHIM_STATE

    @functools.wraps(stream_fn)
    def guarded_stream(self, completion_stream, *args, **kwargs):
        if completion_stream is None:
            raise _api_error(kwargs.get("model") or (args[0] if args else ""),
                             "died before any chunk (None completion_stream)")
        return stream_fn(self, _ZeroChunkGuard(completion_stream, lambda: _api_error(
            kwargs.get("model") or (args[0] if args else ""),
            "ended before any content (empty, unterminated)")), *args, **kwargs)

    guarded_stream._la_bridge_shim = True
    stream_cls.translate_completion_output_params_streaming = guarded_stream

    # the non-stream sibling lives on the messages adapter (response=None -> 'choices' crash)
    nonstream_fn = getattr(messages_cls, "translate_openai_response_to_anthropic", None)
    if nonstream_fn is not None and not getattr(nonstream_fn, "_la_bridge_shim", False):
        try:
            nonstream_guarded = "response is None" in inspect.getsource(nonstream_fn)
        except OSError:
            nonstream_guarded = False
        if not nonstream_guarded:
            @functools.wraps(nonstream_fn)
            def guarded_nonstream(self, response, *args, **kwargs):
                if response is None or getattr(response, "choices", None) is None:
                    raise _api_error(kwargs.get("model") or "",
                                     "returned no response object (None choices)")
                return nonstream_fn(self, response, *args, **kwargs)

            guarded_nonstream._la_bridge_shim = True
            messages_cls.translate_openai_response_to_anthropic = guarded_nonstream

    BRIDGE_SHIM_STATE = "installed"
    print("la_proxy_hooks: bridge shim: installed for this LiteLLM "
          "(upstream #53341 guard absent)", file=sys.stderr)
    return BRIDGE_SHIM_STATE


# ---- fix 8: a fallback retired after the proxy started is skipped, not called -------------

# A proxy's fallback chain is frozen into its YAML at launch; the roster is not. Incident
# 2026-11-23: proxy-4143 (started 11-17) kept calling deepseek-v4.1-flash, retired 11-16 and
# dropped from the roster 11-18 -- a 410 every turn, router retry included, each one a slot in
# the shared NVIDIA RPM window. The hook re-reads the SAME retired list the picker and
# session-fallbacks.py use (re-read only when its mtime moves), and learns a 410 Gone itself.
_GONE_TEXT = re.compile(r"end\s+of\s+life|no\s+longer\s+available", re.I)
_GONE_MODELS: set = set()               # learned from a 410 this proxy saw
_RETIRED_NOTICED: set = set()           # one relaunch notice per model, not one per turn
_RETIRED_CACHE: dict = {"key": None, "ids": frozenset()}


def retired_models_file() -> Path:
    """LA_BROKEN_MODELS_FILE, else the repo's list -- found THROUGH the proxy dir's symlink
    (remote-session.sh links this file there), so a week-old proxy reads today's list."""
    override = os.environ.get("LA_BROKEN_MODELS_FILE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "config" / "broken-nvidia-models.json"


def retired_model_ids() -> frozenset:
    """Ids the retired list names. Missing/corrupt list = empty (fail-open, like
    session-fallbacks.py: a broken list must never block a working model)."""
    f = retired_models_file()
    try:
        st = f.stat()
    except OSError:
        return frozenset()
    key = (str(f), st.st_mtime_ns, st.st_size)
    if _RETIRED_CACHE["key"] != key:
        try:
            import json
            ids = frozenset(m.get("model_id", "") for m in json.loads(f.read_text()).get("models", []))
        except (OSError, ValueError, AttributeError):
            ids = frozenset()
        _RETIRED_CACHE.update(key=key, ids=ids - {""})
    return _RETIRED_CACHE["ids"]


def learn_gone(kwargs: dict) -> str | None:
    """A 410 naming end of life marks the model gone for this proxy; return a log line."""
    exc = (kwargs or {}).get("exception")
    if exc is None:
        return None
    if getattr(exc, "status_code", None) != 410 and not _GONE_TEXT.search(str(exc)):
        return None
    key = _cap_key(kwargs)
    if key in _GONE_MODELS:
        return None
    _GONE_MODELS.add(key)
    return (f"{key} is retired upstream (410 Gone); skipping it with no upstream call for this "
            f"session -- relaunch the session to get the current fallback chain")


def retired_skip(kwargs: dict) -> str | None:
    """Why this call must not go upstream (the model is retired), or None."""
    key = _cap_key(kwargs)
    if key in _GONE_MODELS:
        return f"{key} retired upstream (410 Gone)"
    if key in retired_model_ids():
        return f"{key} retired ({retired_models_file().name})"
    return None


class LAProxyHooks(CustomLogger):
    def __init__(self):
        if CustomLogger is not object:
            super().__init__()
        self._bucket = None            # the nvidia bucket, exactly as before
        self._buckets: dict = {}       # 0.62.0 C2: per-provider buckets (None = unlimited)
        if os.environ.get("LA_PROXY_HOOKS_RATE_LIMIT", "1") != "0":
            from rate_limiter import get_limiter
            self._bucket = get_limiter()

    def _bucket_for(self, kwargs):
        """This deployment's provider bucket (0.62.0 C2). nvidia keeps self._bucket; other
        providers get their own limiter from provider-limits.json; a provider with no
        limits entry gets None = unthrottled, exactly as today."""
        prov = _provider_of(kwargs)
        if prov == "nvidia":
            return self._bucket
        if prov not in self._buckets:
            if os.environ.get("LA_PROXY_HOOKS_RATE_LIMIT", "1") == "0":
                self._buckets[prov] = None
            else:
                from rate_limiter import get_limiter
                try:
                    self._buckets[prov] = get_limiter(provider=prov)
                except Exception as exc:
                    print(f"la_proxy_hooks: no limiter for provider {prov} ({exc}); "
                          f"unthrottled", file=sys.stderr)
                    self._buckets[prov] = None
        return self._buckets[prov]

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        why = retired_skip(kwargs)                       # fix 8: every provider
        if why is not None:
            import litellm
            key = _cap_key(kwargs)
            if key not in _RETIRED_NOTICED:
                _RETIRED_NOTICED.add(key)
                print(f"la_proxy_hooks: {why} -> skipping it with no upstream call; this proxy's "
                      f"fallback chain predates the roster -- relaunch the session to get the "
                      f"current one", file=sys.stderr)
            # Same raise as the 0.40.0 cooling fast-fail (probed on LiteLLM 1.105.1): the router
            # answers from the next fallback at once, with zero upstream calls to this model.
            raise litellm.RateLimitError(message=f"model {why}; skipped without an upstream call",
                                         llm_provider=str(kwargs.get("custom_llm_provider") or ""),
                                         model=str(kwargs.get("model") or ""))
        changed = None
        clamped = clamp_max_tokens(kwargs)               # fix 4: every provider
        if clamped is not None:
            kwargs = changed = clamped
        fixed = apply_request_fixes(kwargs)              # fix 5: every provider
        if fixed is not None:
            kwargs = changed = fixed
        bucket = self._bucket_for(kwargs)
        if bucket is None and not _is_nvidia(kwargs):
            return changed          # non-nvidia without a bucket: unchanged, as before C2
        if bucket is not None:
            model = str(kwargs.get("model") or "")
            cooling = bucket.model_cooling(model)
            if cooling > 0 and os.environ.get("LA_PROXY_FALLBACK_NAMES", ""):
                # 0.40.0 fast-fail: probed on LiteLLM 1.105.1, raising here makes the router
                # answer from the next fallback at once -- ZERO upstream calls to the cooling
                # model, and no 40 RPM slot spent (the fallback needs it). Only with a chain:
                # a chain-less proxy keeps queueing in aacquire exactly as before.
                import litellm
                print(f"la_proxy_hooks: {model} cooling {cooling:.0f}s after 429s "
                      f"-> failing over to a fallback at once", file=sys.stderr)
                fast_fail = litellm.RateLimitError(
                    message=f"model cooling {cooling:.0f}s after consecutive upstream 429s",
                    llm_provider="nvidia_nim", model=model)
                fast_fail.la_self_raised = True   # L8: our own refusal is never booked
                fast_fail.la_cooldown_seconds = cooling
                raise fast_fail
            if getattr(call_type, "value", call_type) == "anthropic_messages":
                # 0.40.1: one /v1/messages request runs this hook TWICE with one call id --
                # here, as it arrives, and again as the translated acompletion that actually
                # goes upstream (probed on LiteLLM 1.105.1). NIM has no native /v1/messages,
                # so the inner call always follows: it books the slot, this one must not, or
                # 40 RPM is really 20. A router retry repeats BOTH layers, so it still books
                # exactly one -- which a dedupe on the call id would have swallowed.
                return normalize_nvidia_kwargs(kwargs)
            try:
                # L1 (0.62.0): pass the model so a cooling model waits out its cooldown
                # inside the queue instead of firing at it. C3: the wait budget is
                # load-stretched (queue depth + active cooldown), capped by the patient tier.
                from rate_limiter import _max_wait, stretched_wait, RateLimitTimeout
                # 0.62.3 review fix: pass tokens= so the quota sidecar (tpm/tpd) actually
                # books this request — 0.62.0 granted with tokens=0 (TPM accounting was
                # limiter-API-only). 0.63.4: the grant returns the reservation's Receipt,
                # recorded here — AFTER the grant, never before — by call id for the
                # success hook. Never in kwargs, which LiteLLM forwards upstream (0.63.1).
                booked = _estimate_tokens(kwargs)
                # mixed fleet: a proxy whose LOADED limiter predates receipts has no
                # aacquire_grant — fall back to the old float API (old accounting, no
                # receipt; the startup warning already said to restart it)
                grant_api = getattr(bucket, "aacquire_grant", None)
                try:
                    if grant_api is not None:
                        waited, receipt = await grant_api(
                            model=str(kwargs.get("model") or ""),
                            max_wait=stretched_wait(_provider_of(kwargs), _max_wait(None)),
                            tokens=booked, kind=_request_kind(kwargs))
                    else:
                        waited = await bucket.aacquire(
                            model=str(kwargs.get("model") or ""),
                            max_wait=stretched_wait(_provider_of(kwargs), _max_wait(None)),
                            tokens=booked, kind=_request_kind(kwargs))
                        receipt = None
                except RateLimitTimeout as exc:
                    if "ineligible" not in str(exc):
                        raise
                    # The ESTIMATE alone can never fit tpm (e.g. an unclamped max_tokens the
                    # provider has not rejected yet): acquire on rpm alone and book nothing —
                    # the upstream 400 must answer and teach the real cap (fix 4), not a
                    # self-raised 429.
                    print(f"la_proxy_hooks: estimated {booked} tokens never fit tpm; "
                          "acquiring on rpm alone", file=sys.stderr)
                    if grant_api is not None:
                        waited, receipt = await grant_api(
                            model=str(kwargs.get("model") or ""),
                            max_wait=stretched_wait(_provider_of(kwargs), _max_wait(None)),
                            tokens=0)
                    else:
                        waited = await bucket.aacquire(
                            model=str(kwargs.get("model") or ""),
                            max_wait=stretched_wait(_provider_of(kwargs), _max_wait(None)),
                            tokens=0)
                        receipt = None
                if receipt is not None:
                    _book_call(kwargs, receipt)      # only AFTER the grant (0.63.4)
                    # the provider the settle must resolve this receipt against: the
                    # success event only knows the RESOLVED model (0.63.4, measured)
                    _note_call_provider(kwargs, _provider_of(kwargs))
            except Exception as exc:
                from rate_limiter import RateLimitTimeout
                if not isinstance(exc, RateLimitTimeout):
                    raise
                # L4 (0.62.0): the bounded wait expired -> retriable 429 to the client,
                # marked self-raised so L8 never books it as an upstream 429.
                import litellm
                e = litellm.RateLimitError(
                    message=f"rate limited: no bucket slot within the wait budget ({exc})",
                    llm_provider="nvidia_nim", model=str(kwargs.get("model") or ""))
                e.la_self_raised = True
                raise e from exc
            if waited > 0:
                print(f"la_proxy_hooks: NVIDIA bucket queued this call {waited:.1f}s",
                      file=sys.stderr)
        # non-nvidia deployments (now bucketed too) keep the pre-C2 return shape: `changed`
        return normalize_nvidia_kwargs(kwargs) if _is_nvidia(kwargs) else changed

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        # 0.63.4: this call's reservation leaves the registry on ANY failure — no settle.
        # A failed call's sidecar booking STAYS (the provider may have counted its tokens);
        # only the receipt reference (and its provider note) is dropped, so a recycled
        # call id can never settle it.
        _take_booked(kwargs or {})
        _take_call_provider(kwargs or {})
        # fix 4, any provider: a 400 naming the provider's max_tokens cap is remembered, so the
        # router's BadRequest retry and every later request go out clamped to it.
        cap = learn_output_cap(kwargs or {})
        if cap is not None:
            print(f"la_proxy_hooks: {_cap_key(kwargs)} rejects max_tokens above {cap}; "
                  f"clamping its requests to {cap} for this session", file=sys.stderr)
        lesson = learn_request_fix(kwargs or {}) if cap is None else None
        if lesson:
            print(f"la_proxy_hooks: {lesson}", file=sys.stderr)
        gone = learn_gone(kwargs or {})                  # fix 8: any provider
        if gone:
            _RETIRED_NOTICED.add(_cap_key(kwargs or {}))
            print(f"la_proxy_hooks: {gone}", file=sys.stderr)
        # Runs once per failed upstream ATTEMPT (router retries included). A 429 is CLASSIFIED
        # (0.40.0): a near-empty window is the provider throttling ONE model -- cool only that
        # model, other proxies run on; a window at least half full is our own RPM overrun --
        # pause every proxy on the machine. Either way, log how many requests the window held:
        # that number locates the real limit.
        bucket = self._bucket_for(kwargs or {})
        if bucket is None:
            return
        exc = (kwargs or {}).get("exception")
        if exc is None or not _is_429(exc):
            return
        call_id = str((kwargs or {}).get("litellm_call_id") or "")
        if getattr(exc, SELF_RAISED_ATTR, False) or _chain_has_self_raised(exc):
            # L8 (0.62.0): OUR OWN refusal (fast-fail, bucket timeout) books nothing and
            # cools nothing -- a request that never left the machine is not an upstream 429,
            # and booking it amplified the very cooldown it reacted to (measured: 1 real
            # upstream request, 3 booked). It DOES mark the call id, so the stream-iterator
            # hook answers the client 429 + Retry-After instead of a 500.
            _record_call_rate_limited(call_id, getattr(exc, "la_cooldown_seconds", None))
            print(f"la_proxy_hooks: self-raised rate-limit refusal not booked (L8); "
                  f"call {call_id[:8]} surfaces as 429", file=sys.stderr)
            return
        model = str((kwargs or {}).get("model") or "")
        info = bucket.note_429(_retry_after_seconds(exc), hidden_attempts=SDK_HIDDEN_RETRIES,
                               model=model)
        _record_call_rate_limited(call_id, info.get("pause"))
        # C2: a quota-classified refusal marks the sidecar dimension exhausted until reset
        if _DAILY_QUOTA.search(str(exc)):
            import datetime
            reset = (datetime.datetime.fromtimestamp(time.time(), datetime.timezone.utc)
                     + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                             microsecond=0)
            bucket.note_quota_refusal("day", reset.timestamp())
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
        # 0.63.4: the success event reports the RESOLVED model ('openai/...' for a
        # groq/openai/... deployment), so the plain _bucket_for() lookup here found no
        # limits entry and the settle never ran (measured 2026-11-25: bookings stayed
        # unrefunded over the real route). Resolve the bucket from the provider the
        # pre-call booked under, falling back to the old lookup.
        prov = _take_call_provider(kwargs or {})
        bucket = self._buckets.get(prov) if prov else None
        if bucket is None:
            bucket = self._bucket_for(kwargs or {})
        if bucket is None:
            return
        # 0.62.3 review fix: reconcile booked-vs-used in the quota sidecar when the response
        # carries usage — 0.62.0 only cleared cooldowns and never refunded the over-estimate.
        usage = response_obj.get("usage") if isinstance(response_obj, dict) \
            else getattr(response_obj, "usage", None)
        used = None
        if usage:
            try:
                if isinstance(usage, dict):
                    used = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
                else:
                    used = int(getattr(usage, "prompt_tokens", 0) or 0) \
                        + int(getattr(usage, "completion_tokens", 0) or 0)
            except (TypeError, ValueError):
                used = None
        receipt = _take_booked(kwargs or {})
        model = str((kwargs or {}).get("model") or "")
        if hasattr(bucket, "aacquire_grant"):
            bucket.note_success(model, tokens_used=used, receipt=receipt)
        else:
            # pre-receipt limiter (mixed fleet): old signature — booked=0 writes nothing
            bucket.note_success(model, tokens_used=used, booked=0)

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
        # 0.50.1 (wifi-cutoff plan): a connect-level failure means the network is down —
        # say THAT plainly instead of surfacing a provider/model error (and its 429 chain).
        if _is_offline(text):
            print(f"la_proxy_hooks: offline: {OFFLINE_DETAIL}", file=sys.stderr)
            for attr, value in (("message", OFFLINE_DETAIL), ("type", "api_connection_error")):
                try:
                    setattr(original_exception, attr, value)
                except Exception:
                    pass
            return None
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

    async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response,
                                                      request_data):
        # C4 (mechanism PROVEN 2026-11-22 on 1.110.2, bridge experiment): on streaming
        # /v1/messages the router's failure hooks are NOT reached with the rate-limit cause
        # before the client stream starts -- the surfacing exception is the PRIMARY's stream
        # error and the client gets 500. This hook wraps the FINAL client-facing iterator:
        # re-yield every chunk; only when the stream dies BEFORE the first chunk AND this
        # call's chain ended on a rate limit (async_log_failure_event recorded the call id)
        # does it raise RateLimitError -> the client gets 429 + Retry-After. After the first
        # chunk the headers are sent, so an error is never changed there.
        import litellm
        yielded = 0
        saw_text = False
        input_tokens: int | None = None

        def _note(chunk):
            nonlocal saw_text, input_tokens
            ev = chunk
            if isinstance(chunk, (bytes, bytearray)):
                for line in str(chunk, "utf-8", "replace").splitlines():
                    if line.startswith("data:"):
                        try:
                            ev = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        break
            if not isinstance(ev, dict):
                return
            if ev.get("type") == "message_start":
                try:
                    input_tokens = int(ev["message"]["usage"].get("input_tokens") or 0)
                except (KeyError, TypeError, ValueError):
                    input_tokens = 0
            if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("text"):
                saw_text = True

        try:
            async for chunk in response:
                yielded += 1
                _note(chunk)
                yield chunk
        except Exception as exc:
            if yielded == 0:
                remaining = _remaining_call_rate_limit(
                    str((request_data or {}).get("litellm_call_id") or ""))
                named = _retry_after_for_chain(exc)
                secs = min([s for s in (remaining, named) if s and s > 0], default=None)
                if remaining is not None:
                    e2 = litellm.RateLimitError(
                        message="rate limited: the fallback chain ended on a rate limit",
                        llm_provider="nvidia_nim",
                        model=str((request_data or {}).get("model") or ""))
                    e2.la_self_raised = True
                    if secs:
                        e2.headers = {"retry-after": str(max(1, int(round(secs))))}
                    print(f"la_proxy_hooks: chain ended on a rate limit -> client 429 "
                          f"(retry-after {secs})", file=sys.stderr)
                    raise e2 from exc
            raise
        if yielded == 0 or (not saw_text and input_tokens == 0):
            # die0 (measured 2026-11-22): the upstream socket dies before any SSE data and
            # LiteLLM's stream wrapper synthesizes 2 empty chunks; the adapter then emits a
            # complete-looking EMPTY message (message_start..message_stop, usage all zero)
            # and the client gets a silent empty 200. Discriminant: no text delta AND the
            # echoed prompt usage is 0 — a real answer always carries input_tokens > 0.
            # This is an error, never a silent empty 200 (invariant 4).
            e3 = litellm.APIConnectionError(
                message="upstream stream ended before any content (empty, unterminated)",
                llm_provider="anthropic",
                model=str((request_data or {}).get("model") or ""))
            e3.status_code = 529
            e3.type = "overloaded_error"
            e3.la_self_raised = True
            raise e3


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
    install_bridge_shim()
    if os.environ.get("LA_PASSTHROUGH_MODELS"):
        register_passthrough_models(os.environ["LA_PASSTHROUGH_MODELS"])
proxy_hooks = LAProxyHooks() if CustomLogger is not object else None


# ---- CLI ------------------------------------------------------------------------------------

def _self_test() -> int:
    ok = True
    print(f"bridge shim: {BRIDGE_SHIM_STATE or 'not-installed'}")
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
