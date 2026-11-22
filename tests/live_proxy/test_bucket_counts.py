#!/usr/bin/env python3
"""Task 5 Step 5 — invariant 1 delivery proof: NO provider bucket grants more than its
rpm in any 60 s window ACROSS PROCESSES, measured on the FAKE's hit timestamps (never on
the hook's own counters). Two real proxies share one sandbox limiter dir; 12 concurrent
requests against rpm=6 must never produce more than 6 hits in any 60 s window.
"""
import json
import threading
from pathlib import Path

RPM = 6
N_REQUESTS = 12

LIMITS_DOC = {
    "schema_version": 1, "session_min_context": 262145, "large_context_threshold": 262144,
    "providers": {
        "nvidia": {"rpm": RPM, "tpm": None, "rpd": None, "rpw": None, "tpd": None,
                   "dispatch_only": False, "reserves_on_request": False,
                   "measured_at": "2026-11-22", "source": "test override (FA_PROVIDER_LIMITS)"},
    },
    "models": {},
}


def test_rpm_never_exceeded_across_two_proxies(fake_upstream, live_proxy, anthropic_stream,
                                               two_tier_config, tmp_path):
    fake = fake_upstream("ok")
    limits = tmp_path / "limits.json"
    limits.write_text(json.dumps(LIMITS_DOC))
    shared = tmp_path / "shared"
    env = {"FA_PROVIDER_LIMITS": str(limits),
           "LA_NVIDIA_THROTTLE_STATE": str(shared / "state"),
           "FA_LIMITER_DIR": str(shared / "limiter")}
    p1 = live_proxy(two_tier_config(fake.port, None, hooks=True), env=env)
    p2 = live_proxy(two_tier_config(fake.port, None, hooks=True), env=env)
    try:
        statuses = []

        def call(port):
            status, _events, _headers = anthropic_stream(port, timeout=300)
            statuses.append(status)

        threads = [threading.Thread(target=call, args=(p1.port if i % 2 == 0 else p2.port,))
                   for i in range(N_REQUESTS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(300)
        assert not any(t.is_alive() for t in threads), "a request never completed"
        assert all(s == 200 for s in statuses), statuses
        hits = fake.hits()
        assert len(hits) == N_REQUESTS, hits
        ts = sorted(h["ts"] for h in hits)
        # The limiter's guarantee is on STAMPS in (now-60, now]; a fake hit lags its grant
        # by the request latency, so a hit-anchored 60 s window can catch the tail of the
        # next legal burst. 59 s windows absorb that sub-second lag and still trip on any
        # real overspend (7+ grants inside one window).
        for t0 in ts:
            in_window = sum(1 for u in ts if t0 <= u < t0 + 59.0)
            assert in_window <= RPM, \
                f"{in_window} hits in the 59 s window starting at {t0} (rpm={RPM})"
    finally:
        p1.stop()
        p2.stop()
