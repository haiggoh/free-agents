#!/usr/bin/env python3
"""
Provider-specific quota header parsers for free-agents.

Each function parses a provider's response headers/usage block into a
normalized QuotaRecord. Per addendum §10.
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .telemetry import (
    QuotaRecord,
    DimensionQuota,
    ComputeUnitsQuota,
    DIM_REQUESTS,
    DIM_TOKENS,
    DIM_CREDITS,
    DIM_COMPUTE_UNITS,
    PROVENANCE_AUTHORITATIVE,
    PROVENANCE_RECONCILED,
    PROVENANCE_ESTIMATED,
    PROVENANCE_UNKNOWN,
    SOURCE_RESPONSE_HEADER,
    SOURCE_USAGE_API,
    SOURCE_LOCAL_LEDGER,
    SOURCE_ERROR,
)


def _parse_int(value: Any) -> Optional[int]:
    """Safely parse int from string or int."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_float(value: Any) -> Optional[float]:
    """Safely parse float from string or float."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return time.time()


# ─── Provider Parsers ────────────────────────────────────────────────────────


def parse_gemini(headers: dict, usage: dict, sole_caller: bool = False) -> QuotaRecord:
    """
    Gemini (addendum §10):
    - Import active model limits from AI Studio/account setup
    - Count plugin requests/tokens locally
    - Mark R only when this plugin is confirmed as sole caller
    - Otherwise E
    - Authoritative exhaustion comes from 429 RESOURCE_EXHAUSTED
    """
    # Gemini uses standard OpenAI-compatible headers via their OpenAI endpoint
    # x-ratelimit-limit-requests, x-ratelimit-remaining-requests
    # x-ratelimit-limit-tokens, x-ratelimit-remaining-tokens
    # reset_at from x-ratelimit-reset (unix timestamp) or daily at 00:00 PT

    record = QuotaRecord(
        provider_id="gemini",
        credential_id="",
        model_id=usage.get("model", "gemini-3.6-flash"),
        scope="account",
        window="day",
        provenance=PROVENANCE_RECONCILED if sole_caller else PROVENANCE_ESTIMATED,
        source=SOURCE_RESPONSE_HEADER,
        observed_at=_iso_now(),
    )

    # Parse headers
    limit_req = _parse_int(headers.get("x-ratelimit-limit-requests"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining-requests"))
    limit_tok = _parse_int(headers.get("x-ratelimit-limit-tokens"))
    rem_tok = _parse_int(headers.get("x-ratelimit-remaining-tokens"))
    reset_ts = _parse_int(headers.get("x-ratelimit-reset"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )

    if limit_tok is not None and rem_tok is not None:
        used_tok = limit_tok - rem_tok
        record.dimensions[DIM_TOKENS] = DimensionQuota(
            limit=limit_tok, used=used_tok, remaining=rem_tok
        )

    if reset_ts:
        record.reset_at = datetime.fromtimestamp(reset_ts, timezone.utc).isoformat()

    record._recompute_binding()
    return record


def parse_groq(headers: dict, usage: dict) -> QuotaRecord:
    """
    Groq (addendum §10):
    - Parse all x-ratelimit-* and retry-after headers
    - Use request headers for RPD
    - Accumulate TPD locally against official model limits
    - Calculate status from the most exhausted dimension
    """
    record = QuotaRecord(
        provider_id="groq",
        credential_id="",
        model_id=usage.get("model", "llama-3.3-70b-versatile"),
        scope="account",
        window="day",
        provenance=PROVENANCE_AUTHORITATIVE,
        source=SOURCE_RESPONSE_HEADER,
        observed_at=_iso_now(),
    )

    # Groq headers: x-ratelimit-limit-requests, x-ratelimit-remaining-requests
    # x-ratelimit-limit-tokens, x-ratelimit-remaining-tokens
    # x-ratelimit-reset-requests, x-ratelimit-reset-tokens (seconds until reset)
    # retry-after (seconds)

    limit_req = _parse_int(headers.get("x-ratelimit-limit-requests"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining-requests"))
    limit_tok = _parse_int(headers.get("x-ratelimit-limit-tokens"))
    rem_tok = _parse_int(headers.get("x-ratelimit-remaining-tokens"))
    reset_req = _parse_int(headers.get("x-ratelimit-reset-requests"))
    reset_tok = _parse_int(headers.get("x-ratelimit-reset-tokens"))
    retry_after = _parse_int(headers.get("retry-after"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )
    if limit_tok is not None and rem_tok is not None:
        used_tok = limit_tok - rem_tok
        record.dimensions[DIM_TOKENS] = DimensionQuota(
            limit=limit_tok, used=used_tok, remaining=rem_tok
        )

    # Use the sooner reset
    reset_times = []
    if reset_req:
        reset_times.append(reset_req)
    if reset_tok:
        reset_times.append(reset_tok)
    if retry_after:
        reset_times.append(retry_after)
    if reset_times:
        soonest = min(reset_times)
        record.reset_at = datetime.fromtimestamp(
            _now_ts() + soonest, timezone.utc
        ).isoformat()

    record._recompute_binding()
    return record


def parse_mistral(headers: dict, usage: dict, has_admin_key: bool = False) -> QuotaRecord:
    """
    Mistral (addendum §10):
    - If an Admin key is configured, query /admin/usage and /admin/rate-limit
    - Otherwise track monthly usage locally
    - Status window is month, not day
    """
    record = QuotaRecord(
        provider_id="mistral",
        credential_id="",
        model_id=usage.get("model", "mistral-large"),
        scope="account",
        window="month",
        provenance=PROVENANCE_AUTHORITATIVE if has_admin_key else PROVENANCE_ESTIMATED,
        source=SOURCE_RESPONSE_HEADER if not has_admin_key else SOURCE_USAGE_API,
        observed_at=_iso_now(),
    )

    # Mistral may return x-ratelimit-* headers
    limit_req = _parse_int(headers.get("x-ratelimit-limit"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining"))
    reset_ts = _parse_int(headers.get("x-ratelimit-reset"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )

    if reset_ts:
        record.reset_at = datetime.fromtimestamp(reset_ts, timezone.utc).isoformat()
    else:
        # Monthly reset - first day of next month
        now = datetime.now(timezone.utc)
        if now.month == 12:
            next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
        record.reset_at = next_month.isoformat()

    record._recompute_binding()
    return record


def parse_openrouter(headers: dict, usage: dict) -> QuotaRecord:
    """
    OpenRouter (addendum §10):
    - Confirm whether account receives 50 or 1,000 free requests/day
    - Count successful :free/openrouter-free requests locally
    - Query /api/v1/key for account metadata
    - Reconcile with 429s
    """
    record = QuotaRecord(
        provider_id="openrouter",
        credential_id="",
        model_id=usage.get("model", "openrouter/free"),
        scope="account",
        window="day",
        provenance=PROVENANCE_ESTIMATED,  # without /api/v1/key, we don't know the limit
        source=SOURCE_RESPONSE_HEADER,
        observed_at=_iso_now(),
    )

    # OpenRouter may return x-ratelimit-* headers
    limit_req = _parse_int(headers.get("x-ratelimit-limit"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining"))
    reset_ts = _parse_int(headers.get("x-ratelimit-reset"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )
        record.provenance = PROVENANCE_AUTHORITATIVE

    if reset_ts:
        record.reset_at = datetime.fromtimestamp(reset_ts, timezone.utc).isoformat()

    record._recompute_binding()
    return record


def parse_cloudflare(headers: dict, usage: dict) -> QuotaRecord:
    """
    Cloudflare Workers AI (addendum §10):
    - Official denominator: 10,000 Neurons/day, reset 00:00 UTC
    - Derive Neurons from response tokens and current per-model Neuron prices
    - Reconcile with Billable Usage API when updated
    - Mark E unless account-wide usage is fully observed
    """
    record = QuotaRecord(
        provider_id="cloudflare",
        credential_id="",
        model_id=usage.get("model", "@cf/meta/llama-3.1-8b-instruct"),
        scope="account",
        window="day",
        provenance=PROVENANCE_ESTIMATED,
        source=SOURCE_LOCAL_LEDGER,
        observed_at=_iso_now(),
    )

    # Cloudflare returns x-neurons-used, x-neurons-limit (or similar)
    # Neuron prices per model are published; we compute from tokens
    neurons_used = _parse_int(headers.get("x-neurons-used"))
    neurons_limit = _parse_int(headers.get("x-neurons-limit"))
    neurons_remaining = _parse_int(headers.get("x-neurons-remaining"))

    if neurons_limit is not None:
        record.provenance = PROVENANCE_AUTHORITATIVE
        if neurons_used is not None and neurons_remaining is not None:
            record.dimensions[DIM_COMPUTE_UNITS] = ComputeUnitsQuota(
                unit="Neurons",
                limit=neurons_limit,
                used=neurons_used,
                remaining=neurons_remaining,
            )
        elif neurons_used is not None:
            record.dimensions[DIM_COMPUTE_UNITS] = ComputeUnitsQuota(
                unit="Neurons",
                limit=neurons_limit,
                used=neurons_used,
                remaining=max(0, neurons_limit - neurons_used),
            )

    # Daily reset at 00:00 UTC
    now = datetime.now(timezone.utc)
    next_midnight = datetime(now.year, now.month, now.day + 1, tzinfo=timezone.utc)
    record.reset_at = next_midnight.isoformat()

    record._recompute_binding()
    return record


def parse_github(headers: dict, usage: dict) -> QuotaRecord:
    """
    GitHub Models (addendum §10):
    - Track per user/model
    - Parse inference response rate-limit headers
    - Do NOT use api.github.com/rate_limit as the Models quota
    - If no daily denominator is returned, show unknown or local-only count
    """
    record = QuotaRecord(
        provider_id="github",
        credential_id="",
        model_id=usage.get("model", "openai/gpt-4o-mini"),
        scope="user-model",
        window="day",
        provenance=PROVENANCE_UNKNOWN,
        source=SOURCE_RESPONSE_HEADER,
        observed_at=_iso_now(),
    )

    # GitHub Models returns rate limit headers on inference endpoint
    limit_req = _parse_int(headers.get("x-ratelimit-limit"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining"))
    reset_ts = _parse_int(headers.get("x-ratelimit-reset"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )
        record.provenance = PROVENANCE_AUTHORITATIVE

    if reset_ts:
        record.reset_at = datetime.fromtimestamp(reset_ts, timezone.utc).isoformat()

    record._recompute_binding()
    return record


def parse_zai(headers: dict, usage: dict) -> QuotaRecord:
    """
    Z.AI (addendum §10):
    - Verify that the selected model remains priced at zero
    - No percentage without a published/live denominator
    - Display free + unknown quota
    - Advance on authoritative limit response
    """
    record = QuotaRecord(
        provider_id="zai",
        credential_id="",
        model_id=usage.get("model", "glm-4.7-flash"),
        scope="account",
        window="unknown",
        provenance=PROVENANCE_UNKNOWN,
        source=SOURCE_RESPONSE_HEADER,
        observed_at=_iso_now(),
    )

    # Z.AI may not publish quota headers; if they do, parse them
    limit_req = _parse_int(headers.get("x-ratelimit-limit"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )
        record.provenance = PROVENANCE_AUTHORITATIVE
        record.window = "day"  # assumed

    record._recompute_binding()
    return record


def parse_siliconflow(headers: dict, usage: dict) -> QuotaRecord:
    """
    SiliconFlow (addendum §10):
    - Verify free model identity and zero balance deduction
    - Import live daily model limit
    - Count locally and reconcile on 429
    """
    record = QuotaRecord(
        provider_id="siliconflow",
        credential_id="",
        model_id=usage.get("model", "Qwen/Qwen2.5-7B-Instruct"),
        scope="account",
        window="day",
        provenance=PROVENANCE_ESTIMATED,
        source=SOURCE_RESPONSE_HEADER,
        observed_at=_iso_now(),
    )

    limit_req = _parse_int(headers.get("x-ratelimit-limit"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining"))
    limit_tok = _parse_int(headers.get("x-ratelimit-limit-tokens"))
    rem_tok = _parse_int(headers.get("x-ratelimit-remaining-tokens"))
    reset_ts = _parse_int(headers.get("x-ratelimit-reset"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )
    if limit_tok is not None and rem_tok is not None:
        used_tok = limit_tok - rem_tok
        record.dimensions[DIM_TOKENS] = DimensionQuota(
            limit=limit_tok, used=used_tok, remaining=rem_tok
        )

    if reset_ts:
        record.reset_at = datetime.fromtimestamp(reset_ts, timezone.utc).isoformat()

    record._recompute_binding()
    return record


def parse_llm7(headers: dict, usage: dict) -> QuotaRecord:
    """
    LLM7 (addendum §10):
    - Free token: 1M tokens per rolling 24h
    - Track rolling token events and 100 requests/hour
    - Expire each event at its own timestamp + window
    """
    record = QuotaRecord(
        provider_id="llm7",
        credential_id="",
        model_id=usage.get("model", "gpt-4o-mini"),
        scope="account",
        window="rolling-24h",
        provenance=PROVENANCE_AUTHORITATIVE,  # known denominator
        source=SOURCE_RESPONSE_HEADER,
        observed_at=_iso_now(),
    )

    # LLM7 returns x-ratelimit-* for both dimensions
    limit_req = _parse_int(headers.get("x-ratelimit-limit-requests"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining-requests"))
    limit_tok = _parse_int(headers.get("x-ratelimit-limit-tokens"))
    rem_tok = _parse_int(headers.get("x-ratelimit-remaining-tokens"))
    reset_req = _parse_int(headers.get("x-ratelimit-reset-requests"))
    reset_tok = _parse_int(headers.get("x-ratelimit-reset-tokens"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )
    if limit_tok is not None and rem_tok is not None:
        used_tok = limit_tok - rem_tok
        record.dimensions[DIM_TOKENS] = DimensionQuota(
            limit=limit_tok, used=used_tok, remaining=rem_tok
        )
    else:
        # Known denominator: 1M tokens / rolling 24h
        record.dimensions[DIM_TOKENS] = DimensionQuota(
            limit=1_000_000, used=None, remaining=None
        )

    # Use the sooner reset
    reset_times = []
    if reset_req:
        reset_times.append(reset_req)
    if reset_tok:
        reset_times.append(reset_tok)
    if reset_times:
        soonest = min(reset_times)
        record.reset_at = datetime.fromtimestamp(
            _now_ts() + soonest, timezone.utc
        ).isoformat()

    record._recompute_binding()
    return record


def parse_kilo(headers: dict, usage: dict) -> QuotaRecord:
    """
    Kilo (addendum §10):
    - Track 200 free requests per rolling hour per IP
    - Because scope is shared IP, use E and ≤ unless exclusive IP use is known
    """
    record = QuotaRecord(
        provider_id="kilo",
        credential_id="",
        model_id=usage.get("model", "kilo-auto/free"),
        scope="ip-model",
        window="rolling-hour",
        provenance=PROVENANCE_ESTIMATED,
        source=SOURCE_LOCAL_LEDGER,
        observed_at=_iso_now(),
    )

    # Kilo may return headers; if not, use known limit
    limit_req = _parse_int(headers.get("x-ratelimit-limit"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining"))
    reset_ts = _parse_int(headers.get("x-ratelimit-reset"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )
        record.provenance = PROVENANCE_AUTHORITATIVE
    else:
        # Known: 200 requests / rolling hour per IP
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=200, used=None, remaining=None
        )

    if reset_ts:
        record.reset_at = datetime.fromtimestamp(reset_ts, timezone.utc).isoformat()
    else:
        # Rolling hour - next hour boundary
        now = datetime.now(timezone.utc)
        next_hour = datetime(now.year, now.month, now.day, now.hour + 1, tzinfo=timezone.utc)
        record.reset_at = next_hour.isoformat()

    record._recompute_binding()
    return record


def parse_vercel(headers: dict, usage: dict) -> QuotaRecord:
    """
    Vercel AI Gateway (addendum §10):
    - Track providerMetadata.gateway.cost from each response
    - Reconcile against the account's monthly free-credit balance
    - Purchasing paid Gateway credits ends eligibility for monthly free credit
    """
    record = QuotaRecord(
        provider_id="vercel",
        credential_id="",
        model_id=usage.get("model", "vercel/ai-gateway"),
        scope="account",
        window="month",
        provenance=PROVENANCE_ESTIMATED,
        source=SOURCE_RESPONSE_HEADER,
        observed_at=_iso_now(),
    )

    # Vercel returns providerMetadata.gateway.cost in response body
    # Also may have x-ratelimit-* headers
    limit_req = _parse_int(headers.get("x-ratelimit-limit"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining"))
    limit_credits = _parse_int(headers.get("x-ratelimit-limit-credits"))
    rem_credits = _parse_int(headers.get("x-ratelimit-remaining-credits"))
    reset_ts = _parse_int(headers.get("x-ratelimit-reset"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )
    if limit_credits is not None and rem_credits is not None:
        used_credits = limit_credits - rem_credits
        record.dimensions[DIM_CREDITS] = DimensionQuota(
            limit=limit_credits, used=used_credits, remaining=rem_credits
        )
        record.provenance = PROVENANCE_AUTHORITATIVE

    if reset_ts:
        record.reset_at = datetime.fromtimestamp(reset_ts, timezone.utc).isoformat()
    else:
        # Monthly reset
        now = datetime.now(timezone.utc)
        if now.month == 12:
            next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
        record.reset_at = next_month.isoformat()

    record._recompute_binding()
    return record


def parse_sambanova(headers: dict, usage: dict) -> QuotaRecord:
    """
    SambaNova (addendum §10):
    - Use current SambaNova OpenAI-compatible base URL from live account/docs
    - Do not hard-code historical free limits
    - Qualify from live response headers
    """
    record = QuotaRecord(
        provider_id="sambanova",
        credential_id="",
        model_id=usage.get("model", "Meta-Llama-3.1-70B-Instruct"),
        scope="account",
        window="unknown",
        provenance=PROVENANCE_UNKNOWN,
        source=SOURCE_RESPONSE_HEADER,
        observed_at=_iso_now(),
    )

    # Parse whatever headers are present
    limit_req = _parse_int(headers.get("x-ratelimit-limit"))
    rem_req = _parse_int(headers.get("x-ratelimit-remaining"))
    reset_ts = _parse_int(headers.get("x-ratelimit-reset"))

    if limit_req is not None and rem_req is not None:
        used_req = limit_req - rem_req
        record.dimensions[DIM_REQUESTS] = DimensionQuota(
            limit=limit_req, used=used_req, remaining=rem_req
        )
        record.provenance = PROVENANCE_AUTHORITATIVE

    if reset_ts:
        record.reset_at = datetime.fromtimestamp(reset_ts, timezone.utc).isoformat()

    record._recompute_binding()
    return record


def parse_modelscope(headers: dict, usage: dict) -> QuotaRecord:
    """
    ModelScope (addendum §10):
    - Keep disabled until live qualification confirms quota and regional access
    """
    record = QuotaRecord(
        provider_id="modelscope",
        credential_id="",
        model_id=usage.get("model", "unknown"),
        scope="unknown",
        window="unknown",
        provenance=PROVENANCE_UNKNOWN,
        source=SOURCE_ERROR,
        observed_at=_iso_now(),
    )
    # Disabled - no parsing
    record._recompute_binding()
    return record


# ─── Dispatcher ───────────────────────────────────────────────────────────────


PARSERS = {
    "gemini": parse_gemini,
    "groq": parse_groq,
    "mistral": parse_mistral,
    "openrouter": parse_openrouter,
    "cloudflare": parse_cloudflare,
    "github": parse_github,
    "zai": parse_zai,
    "siliconflow": parse_siliconflow,
    "llm7": parse_llm7,
    "kilo": parse_kilo,
    "vercel": parse_vercel,
    "sambanova": parse_sambanova,
    "modelscope": parse_modelscope,
}


def parse_provider(provider_id: str, headers: dict, usage: dict, **kwargs) -> QuotaRecord:
    """Dispatch to the appropriate provider parser."""
    parser = PARSERS.get(provider_id.lower())
    if parser is None:
        # Fallback: generic parser
        record = QuotaRecord(
            provider_id=provider_id,
            credential_id="",
            model_id=usage.get("model", "unknown"),
            provenance=PROVENANCE_UNKNOWN,
            source=SOURCE_RESPONSE_HEADER,
            observed_at=_iso_now(),
        )
        limit_req = _parse_int(headers.get("x-ratelimit-limit"))
        rem_req = _parse_int(headers.get("x-ratelimit-remaining"))
        if limit_req is not None and rem_req is not None:
            used_req = limit_req - rem_req
            record.dimensions[DIM_REQUESTS] = DimensionQuota(
                limit=limit_req, used=used_req, remaining=rem_req
            )
            record.provenance = PROVENANCE_AUTHORITATIVE
        record._recompute_binding()
        return record
    return parser(headers, usage, **kwargs)


if __name__ == "__main__":
    # Test with mock data
    import sys
    test_headers = {
        "x-ratelimit-limit-requests": "100",
        "x-ratelimit-remaining-requests": "59",
        "x-ratelimit-limit-tokens": "10000",
        "x-ratelimit-remaining-tokens": "8500",
        "x-ratelimit-reset": str(int(_now_ts()) + 3600),
    }
    test_usage = {"model": "gemini-3.6-flash"}
    result = parse_provider("gemini", test_headers, test_usage, sole_caller=True)
    print(json.dumps(result.to_dict(), indent=2))