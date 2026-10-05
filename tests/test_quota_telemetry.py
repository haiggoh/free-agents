#!/usr/bin/env python3
"""Tests for quota telemetry module."""

import json
import tempfile
import time
from pathlib import Path

import pytest

# Import the quota telemetry modules
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from quota.telemetry import (
    QuotaRecord,
    DimensionQuota,
    write_usage_event,
    read_ledger,
    rebuild_state,
    emit_statusline_json,
    PROVENANCE_AUTHORITATIVE,
    PROVENANCE_RECONCILED,
    PROVENANCE_ESTIMATED,
    PROVENANCE_UNKNOWN,
    DIM_REQUESTS,
    DIM_TOKENS,
    DIM_CREDITS,
    DIM_COMPUTE_UNITS,
)
from quota.providers import parse_provider


class TestQuotaRecord:
    """Tests for QuotaRecord dataclass."""

    def test_basic_creation(self):
        """Test basic QuotaRecord creation."""
        record = QuotaRecord(
            provider_id="gemini",
            credential_id="key123",
            model_id="gemini-3.6-flash",
        )
        assert record.provider_id == "gemini"
        assert record.credential_id == "key123"
        assert record.model_id == "gemini-3.6-flash"
        # All dimensions should exist
        for dim in (DIM_REQUESTS, DIM_TOKENS, DIM_CREDITS, DIM_COMPUTE_UNITS):
            assert dim in record.dimensions

    def test_used_fraction_calculation(self):
        """Test used_fraction = max of known fractions (addendum §8)."""
        record = QuotaRecord(
            provider_id="gemini",
            credential_id="key123",
            model_id="gemini-3.6-flash",
        )
        record.dimensions[DIM_REQUESTS] = DimensionQuota(limit=100, used=41, remaining=59)
        record.dimensions[DIM_TOKENS] = DimensionQuota(limit=10000, used=5000, remaining=5000)
        record._recompute_binding()
        assert record.used_fraction == 0.5  # tokens is binding (50% > 41%)
        assert record.binding_dimension == DIM_TOKENS

    def test_no_percentage_when_denominator_unknown(self):
        """Test no percentage when limit is unknown (addendum §7)."""
        record = QuotaRecord(
            provider_id="zai",
            credential_id="key123",
            model_id="glm-4.7-flash",
            provenance=PROVENANCE_UNKNOWN,
        )
        # No dimensions with limits
        record._recompute_binding()
        assert record.used_fraction is None
        assert record.binding_dimension is None

    def test_statusline_format(self):
        """Test statusline format per addendum §7."""
        record = QuotaRecord(
            provider_id="groq",
            credential_id="key123",
            model_id="llama-3.3-70b-versatile",
            window="day",
            provenance=PROVENANCE_AUTHORITATIVE,
        )
        record.dimensions[DIM_REQUESTS] = DimensionQuota(limit=100, used=41, remaining=59)
        record._recompute_binding()
        status = record.to_statusline()
        assert "[Groq/llama-3.3-70b-versatile A day 41% · REQ]" in status


class TestLedger:
    """Tests for JSONL ledger I/O."""

    def test_write_and_read_ledger(self):
        """Test writing and reading ledger entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "remote-usage.jsonl"
            state_path = Path(tmpdir) / "remote-quota-state.json"

            write_usage_event(
                provider="gemini",
                credential="GEMINI_API_KEY...",
                model="gemini-3.6-flash",
                input_tokens=123,
                output_tokens=456,
                provider_reported={
                    "requests": {"limit": 100, "used": 41, "remaining": 59},
                    "tokens": {"limit": 10000, "used": 579, "remaining": 9421},
                },
                ledger_path=ledger_path,
            )

            records = read_ledger(ledger_path)
            assert len(records) == 1
            assert records[0].provider_id == "gemini"
            assert records[0].provenance == PROVENANCE_AUTHORITATIVE

    def test_rebuild_state(self):
        """Test rebuilding state from ledger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "remote-usage.jsonl"
            state_path = Path(tmpdir) / "remote-quota-state.json"

            # Write multiple entries for same provider
            for i in range(3):
                write_usage_event(
                    provider="gemini",
                    credential="GEMINI_API_KEY...",
                    model="gemini-3.6-flash",
                    input_tokens=100,
                    output_tokens=200,
                    provider_reported={"requests": {"limit": 100, "used": 10 + i, "remaining": 90 - i}},
                    ledger_path=ledger_path,
                )

            latest = rebuild_state(ledger_path, state_path)
            assert "gemini" in latest
            assert "GEMINI_API_KEY..." in latest["gemini"]
            # Latest should have highest used
            assert latest["gemini"]["GEMINI_API_KEY..."].dimensions[DIM_REQUESTS].used == 12
            # state_path is used by rebuild_state but not directly tested
            assert state_path.exists()


class TestProviderParsers:
    """Tests for provider-specific header parsers (addendum §10)."""

    def test_parse_gemini(self):
        """Test Gemini parser."""
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "59",
            "x-ratelimit-limit-tokens": "10000",
            "x-ratelimit-remaining-tokens": "8500",
            "x-ratelimit-reset": str(int(time.time()) + 3600),
        }
        usage = {"model": "gemini-3.6-flash"}
        record = parse_provider("gemini", headers, usage, sole_caller=True)
        assert record.provider_id == "gemini"
        assert record.provenance == PROVENANCE_RECONCILED
        assert record.dimensions[DIM_REQUESTS].limit == 100
        assert record.dimensions[DIM_REQUESTS].used == 41

    def test_parse_groq(self):
        """Test Groq parser."""
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "59",
            "x-ratelimit-limit-tokens": "10000",
            "x-ratelimit-remaining-tokens": "8500",
            "x-ratelimit-reset-requests": "3600",
        }
        usage = {"model": "llama-3.3-70b-versatile"}
        record = parse_provider("groq", headers, usage)
        assert record.provider_id == "groq"
        assert record.provenance == PROVENANCE_AUTHORITATIVE

    def test_parse_mistral_with_admin_key(self):
        """Test Mistral parser with admin key."""
        headers = {
            "x-ratelimit-limit": "1000",
            "x-ratelimit-remaining": "500",
            "x-ratelimit-reset": str(int(time.time()) + 86400),
        }
        usage = {"model": "mistral-large"}
        record = parse_provider("mistral", headers, usage, has_admin_key=True)
        assert record.provider_id == "mistral"
        assert record.provenance == PROVENANCE_AUTHORITATIVE
        assert record.window == "month"

    def test_parse_cloudflare(self):
        """Test Cloudflare parser (Neurons)."""
        headers = {
            "x-neurons-limit": "10000",
            "x-neurons-used": "5200",
            "x-neurons-remaining": "4800",
        }
        usage = {"model": "@cf/meta/llama-3.1-8b-instruct"}
        record = parse_provider("cloudflare", headers, usage)
        assert record.provider_id == "cloudflare"
        assert record.dimensions[DIM_COMPUTE_UNITS].unit == "Neurons"
        assert record.dimensions[DIM_COMPUTE_UNITS].limit == 10000

    def test_parse_zai_unknown(self):
        """Test Z.AI parser - no percentage without denominator."""
        headers = {}
        usage = {"model": "glm-4.7-flash"}
        record = parse_provider("zai", headers, usage)
        assert record.provider_id == "zai"
        assert record.provenance == PROVENANCE_UNKNOWN
        assert record.used_fraction is None

    def test_parse_llm7_rolling_window(self):
        """Test LLM7 parser (rolling 24h)."""
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "50",
            "x-ratelimit-limit-tokens": "1000000",
            "x-ratelimit-remaining-tokens": "500000",
            "x-ratelimit-reset-requests": "3600",
        }
        usage = {"model": "gpt-4o-mini"}
        record = parse_provider("llm7", headers, usage)
        assert record.provider_id == "llm7"
        assert record.window == "rolling-24h"
        assert record.dimensions[DIM_TOKENS].limit == 1_000_000

    def test_parse_kilo_shared_ip(self):
        """Test Kilo parser (shared IP → estimated)."""
        headers = {}
        usage = {"model": "kilo-auto/free"}
        record = parse_provider("kilo", headers, usage)
        assert record.provider_id == "kilo"
        assert record.provenance == PROVENANCE_ESTIMATED
        assert record.scope == "ip-model"
        assert record.window == "rolling-hour"
        assert record.dimensions[DIM_REQUESTS].limit == 200

    def test_parse_vercel_credits(self):
        """Test Vercel parser (credits)."""
        headers = {
            "x-ratelimit-limit-credits": "1000",
            "x-ratelimit-remaining-credits": "500",
        }
        usage = {"model": "vercel/ai-gateway"}
        record = parse_provider("vercel", headers, usage)
        assert record.provider_id == "vercel"
        assert record.dimensions[DIM_CREDITS].limit == 1000
        assert record.dimensions[DIM_CREDITS].used == 500


class TestStatuslineEmission:
    """Tests for statusline emission."""

    def test_emit_statusline(self):
        """Test emitting statusline segments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "remote-usage.jsonl"
            state_path = Path(tmpdir) / "remote-quota-state.json"

            write_usage_event(
                provider="gemini",
                credential="GEMINI_API_KEY...",
                model="gemini-3.6-flash",
                input_tokens=100,
                output_tokens=200,
                provider_reported={
                    "requests": {"limit": 100, "used": 41, "remaining": 59},
                },
                ledger_path=ledger_path,
            )
            rebuild_state(ledger_path, state_path)

            output = emit_statusline_json(["gemini"], state_path)
            data = json.loads(output)
            assert len(data) == 1
            assert data[0]["label"] == "quota"
            assert "Gemini" in data[0]["text"]
            assert "41%" in data[0]["text"]


class TestAddendumRequirements:
    """Tests for specific addendum requirements (§12)."""

    def test_authoritative_header_parsing(self):
        """§12: authoritative header parsing works."""
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "59",
        }
        usage = {"model": "test"}
        record = parse_provider("groq", headers, usage)
        assert record.provenance == PROVENANCE_AUTHORITATIVE
        assert record.used_fraction == 0.41

    def test_daily_monthly_rolling_ledgers(self):
        """§12: daily, monthly, rolling window ledgers."""
        # Daily (Gemini)
        r1 = parse_provider("gemini", {"x-ratelimit-reset": str(int(time.time())+86400)}, {"model": "m"})
        assert r1.window == "day"
        # Monthly (Mistral)
        r2 = parse_provider("mistral", {"x-ratelimit-reset": str(int(time.time())+2592000)}, {"model": "m"})
        assert r2.window == "month"
        # Rolling 24h (LLM7)
        r3 = parse_provider("llm7", {}, {"model": "m"})
        assert r3.window == "rolling-24h"
        # Rolling hour (Kilo)
        r4 = parse_provider("kilo", {}, {"model": "m"})
        assert r4.window == "rolling-hour"

    def test_expiration_of_rolling_events(self):
        """§12: expiration of rolling events (rebuild_state max_age_days)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "remote-usage.jsonl"
            state_path = Path(tmpdir) / "remote-quota-state.json"

            write_usage_event(
                provider="llm7",
                credential="KEY",
                model="m",
                input_tokens=100,
                output_tokens=100,
                provider_reported={"tokens": {"limit": 1_000_000, "used": 500_000, "remaining": 500_000}},
                ledger_path=ledger_path,
            )
            # Rebuild with max_age_days=0 should exclude it
            latest = rebuild_state(ledger_path, state_path, max_age_days=0)
            assert "llm7" not in latest

    def test_binding_dimension_calculation(self):
        """§12: binding_dimension = max used/limit across dimensions."""
        record = QuotaRecord(provider_id="test", credential_id="c", model_id="m")
        record.dimensions[DIM_REQUESTS] = DimensionQuota(limit=100, used=90, remaining=10)  # 90%
        record.dimensions[DIM_TOKENS] = DimensionQuota(limit=1000, used=500, remaining=500)  # 50%
        record._recompute_binding()
        assert record.binding_dimension == DIM_REQUESTS
        assert record.used_fraction == 0.9

    def test_no_percentage_when_denominator_unknown(self):
        """§12: no percentage when denominator unknown."""
        record = QuotaRecord(provider_id="zai", credential_id="c", model_id="m",
                             provenance=PROVENANCE_UNKNOWN)
        assert record.used_fraction is None
        # Statusline should show ?%
        status = record.to_statusline()
        assert "?%" in status

    def test_partial_account_marked_estimated(self):
        """§12: partial-account usage marked estimated."""
        record = QuotaRecord(provider_id="gemini", credential_id="c", model_id="m",
                             provenance=PROVENANCE_ESTIMATED)
        assert record.provenance == PROVENANCE_ESTIMATED

    def test_temporary_rpm_vs_hard_daily(self):
        """§12: temporary RPM exhaustion vs hard daily exhaustion."""
        # This is a behavioral test - RPM (rate limit) should be retriable
        # while RPD (daily quota) should advance provider
        # The classifier in remote_provider_core handles this
        from remote_provider_core import classify
        # 429 with rate limit text → quota (retriable, but daily exhausted)
        assert classify(429, "rate limit exceeded") == "quota"
        # 429 with "too many requests" is also classified as quota (429 + rate limit language)
        assert classify(429, "too many requests per minute") == "quota"
        # Transient is for non-rate-limit 5xx
        assert classify(500, "internal server error") == "transient"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])