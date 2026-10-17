#!/usr/bin/env python3
"""Shared core for the remote-fallback lane of local-agents.

This is the ONLY place that knows what a provider is. `remote-agent-dispatch.py`
(the OpenAI-compatible HTTP client), `remote-provider-doctor.py` (connectivity /
quota checks) and `agent-fallback.py` (the router) all import from here so that
the provider model — its endpoints, its tier, its tiering policy — cannot drift
between the three tools.

Privacy posture (non-negotiable): the remote lane is DISABLED by default.
Nothing in this module ever makes a network call on its own. A caller must
explicitly select a provider and a routing mode that permits it. Local-only is
the default; remote is an opt-in the user has to ask for.

This module is import-only: running it directly just prints the provider
definitions (a handy way to inspect them) and exits 0. It has no side effects.
"""
import json
import os
import sys
import time
from dataclasses import dataclass, field

SCHEMA_VERSION = 1


# --- provider tiers ----------------------------------------------------------
# These are the only values a provider's `tier` may take. The tiering policy
# (which tiers a given routing mode is allowed to touch) lives in
# agent-fallback.py. The distinction that matters most is RENEWING_FREE vs
# TRIAL vs PAID — the router must never silently spend a trial or a dollar.
LOCAL = "local"
RENEWING_FREE = "renewing_free"   # a free tier that resets on a schedule (daily/monthly)
TRIAL = "trial"                    # one-time or limited allowance; NOT permanently free
PAID = "paid"
CONSUMER_WEB = "consumer_web"      # e.g. a logged-in web app, not an API we should drive
UNKNOWN = "unknown"

TIER_CHOICES = (LOCAL, RENEWING_FREE, TRIAL, PAID, CONSUMER_WEB, UNKNOWN)


@dataclass(frozen=True)
class Provider:
    """A remote provider endpoint. Identity + how to reach it + its cost tier.

    `key_env` is the NAME of the environment variable that holds the key — the
    key itself is read from the environment at call time, never stored here or
    written to a config file (config constraint: secrets only via env vars).
    `extra_env` names additional env vars required (e.g. an account id).
    """
    id: str
    display: str
    tier: str
    base_url: str                  # no trailing slash
    chat_path: str                 # e.g. "/chat/completions"
    models_path: str               # e.g. "/models"; may be a full URL (GitHub)
    key_env: str
    extra_env: tuple = ()
    static_headers: dict = field(default_factory=dict)
    catalog_url: str = ""          # optional: a separate catalog endpoint (GitHub)
    default_models: tuple = ()     # known-good model ids, in preference order
    supports_responses: bool = False
    notes: str = ""

    @property
    def key_name(self):
        return self.key_env

    def required_env(self):
        """All env var names this provider needs (key + extras)."""
        return (self.key_env,) + tuple(self.extra_env)

    def key(self):
        """The API key: the environment variable if set, else the canonical key file
        ``${LA_API_KEYS_DIR:-~/.api_keys}/<provider id>`` (the same store remote-session.sh
        reads). Read at call time and never cached, logged or written anywhere."""
        val = os.environ.get(self.key_env, "").strip()
        if val:
            return val
        path = os.path.join(os.environ.get("LA_API_KEYS_DIR") or
                            os.path.expanduser("~/.api_keys"), self.id)
        try:
            with open(path) as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def auth_headers(self):
        """The Authorization / version headers, built from the live environment."""
        key = self.key()
        return {"Authorization": "Bearer " + key,
                **self.static_headers}

    def base(self):
        """The base URL with any env-var placeholders (e.g. a Cloudflare account id) filled in."""
        url = self.base_url
        for name in self.required_env():
            url = url.replace("{%s}" % name, os.environ.get(name, ""))
        return url.rstrip("/")

    def config_present(self):
        """True iff every required env var is non-empty. Never touches the network."""
        if not self.key():
            return False
        return all(os.environ.get(n, "").strip() != "" for n in self.extra_env)

    @property
    def has_catalog(self):
        """True iff the provider exposes a callable catalog endpoint (models_path or catalog_url)."""
        return bool(self.catalog_url or self.models_path)


# Provider data is loaded from config/remote-registry.yaml by
# remote_provider_registry.py. Re-export everything from there so that
# existing importers (remote-agent-dispatch.py, remote-provider-doctor.py,
# remote-probe-log.py, agent-fallback.py) continue to work without changes.
from remote_provider_registry import (  # noqa: F401,F403
    PROVIDERS,
    IMPLEMENTED,
    RATE_LIMITED,
    CATALOG_SCANABLE,
    ENTRIES,
)


def get(provider_id):
    return PROVIDERS.get(provider_id)


# --- the structured result record -------------------------------------------
# A dispatch — local or remote — returns one of these. It is the single
# vocabulary that the router, the skill, and any ledger build on, so "what
# actually happened" is machine-readable rather than prose.
@dataclass
class Result:
    ok: bool
    provider: str                 # "local" for the local lane
    tier: str                     # a provider tier, or LOCAL
    model_requested: str
    model_effective: str
    status: int = 0               # HTTP status (0 = no response / local)
    error_class: str = ""         # "" | quota | transient | timeout | model_unavailable |
                                  #   auth | malformed | policy | partial | transport | ok
    error_reason: str = ""        # human-readable failure reason (the exact one)
    usage: dict = field(default_factory=dict)   # the provider's usage block, verbatim
    partial: bool = False         # output started then the stream was cut
    stream_complete: bool = False # saw the terminal [DONE] / finished cleanly
    output_chars: int = 0
    output_path: str = ""
    elapsed: float = 0.0
    retry_after: float = 0.0      # seconds, from a Retry-After header if the provider sent one
    attempts: int = 1

    @property
    def retriable(self):
        """True iff the router is allowed to fail over / retry on THIS result.

        The plan is explicit: fall back only on quota, transient failure,
        timeout-before-output, or model unavailability. Never on auth, malformed
        request, policy refusal, or partial output. Encoded here so no caller can
        drift.
        """
        if self.ok:
            return False
        return self.error_class in ("quota", "transient", "timeout", "model_unavailable")

    def to_dict(self):
        return {
            "schema_version": SCHEMA_VERSION, "ok": self.ok,
            "provider": self.provider, "tier": self.tier,
            "model_requested": self.model_requested,
            "model_effective": self.model_effective,
            "status": self.status, "error_class": self.error_class,
            "error_reason": self.error_reason, "usage": self.usage,
            "partial": self.partial, "stream_complete": self.stream_complete,
            "output_chars": self.output_chars, "output_path": self.output_path,
            "elapsed": round(self.elapsed, 3), "retry_after": self.retry_after,
            "attempts": self.attempts,
        }


def classify(status, body="", error=""):
    """Map an HTTP status + body to a Result error_class + retriable verdict.

    This is the heart of the failover policy. It deliberately returns `partial`
    for "we started streaming then it broke" — the caller knows that from the
    stream state; the classification here only sees the terminal status.
    """
    text = (body or "") + " " + (error or "")
    low = text.lower()
    if status in (408, 409, 429, 500, 502, 503, 504):
        # 429/5xx are the transient family. But a 500 that is really a bad request
        # or a 4xx-ish refusal is NOT retriable — look at the body to split them.
        if any(t in low for t in ("rate limit", "rate_limit", "quota", "too many",
                                  "exceeded", "resource exhausted", "overloaded")):
            return "quota" if status == 429 else "transient"
        if any(t in low for t in ("model not found", "not found", "no such model",
                                  "does not exist", "unavailable model")):
            return "model_unavailable"
        if any(t in low for t in ("connection reset", "timed out", "timeout",
                                  "etimedout", "econnreset")):
            return "transient"
        # A bare 500/502/503 with no distinguishing body: treat as transient,
        # BUT the router's hard attempt ceiling prevents a retry storm.
        return "transient"
    if status == 401 or status == 403:
        return "auth"
    if status == 413:
        return "auth"  # too large — not a provider we should retry
    if status == 400 or status == 422:
        # A 400 is usually a malformed *request* (not retriable) — but providers
        # express some other outcomes as 400 too, and those must be told apart:
        # a safety/policy refusal and a "model doesn't exist" are each their own
        # class. Check the refusal first, then the missing-model wording.
        if any(t in low for t in ("safety", "policy", "blocked", "prohibited",
                                  "filtered", "not allowed")):
            return "policy"
        if any(t in low for t in ("model not found", "not found", "does not exist",
                                  "unavailable")):
            return "model_unavailable"
        return "malformed"
    if status == 404:
        # /models 404 is model_unavailable; a chat 404 is usually a bad path/model.
        return "model_unavailable" if "model" in low else "malformed"
    if status == 410 or ("safety" in low or "policy" in low or "blocked" in low
                         or "prohibited" in low or "filtered" in low):
        return "policy"
    if status >= 500:
        return "transient"
    if 400 <= status < 500:
        return "malformed"
    return ""  # 2xx → the caller sets ok=True; error_class stays ""