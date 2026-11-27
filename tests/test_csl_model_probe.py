#!/usr/bin/env python3
"""tests/test_csl_model_probe.py — R5 Task 1: the unreachable-model probe engine.

Contract (waypoint free-agents-detect-listed-but, user design 2026-11-24):
- 16-token probe, deadline measured FROM PROVIDER SEND (queued/cooldown time never counts).
- limiter gate refuses the send -> "unknown" (never "unreachable"), send not attempted.
- provider answers 200 -> "reachable"; provider silent past the deadline -> "unreachable";
  429/other send failures -> "unknown".
- marks live under the R1-A config dir, carry a TTL, and EXPIRE (dropped on load past TTL) —
  a still-listed model is "temporarily" unreachable, never "retired".
"""
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
spec = importlib.util.spec_from_file_location("csl_model_probe", ROOT / "bin" / "csl-model-probe.py")
probe = importlib.util.module_from_spec(spec)
sys.modules["csl_model_probe"] = probe
spec.loader.exec_module(probe)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()

    def _probe(self, send, gate=lambda: True, deadline_s=20.0):
        return probe.probe_alias("nvidia-glm53", send=send, gate=gate,
                                 deadline_s=deadline_s, clock=self.clock.monotonic)

    def test_reachable_when_provider_answers(self):
        r = self._probe(send=lambda: ("200", b"ok"))
        self.assertEqual(r["state"], "reachable")

    def test_unreachable_when_provider_silent_past_deadline(self):
        def send():
            self.clock.now += 25.0          # provider never answers within the deadline
            raise TimeoutError("no answer")
        r = self._probe(send=send, deadline_s=20.0)
        self.assertEqual(r["state"], "unreachable")

    def test_unknown_when_limiter_gate_refuses_and_send_not_attempted(self):
        calls = []
        r = self._probe(send=lambda: calls.append(1) or ("200", b""), gate=lambda: False)
        self.assertEqual(r["state"], "unknown")
        self.assertEqual(calls, [], "a probe that could not be sent must not send")

    def test_unknown_on_429(self):
        r = self._probe(send=lambda: ("429", b""))
        self.assertEqual(r["state"], "unknown")

    def test_queued_time_never_counts_against_the_deadline(self):
        # the gate "waits" 25 s in the queue (fake clock — LONGER than the 20 s deadline),
        # then allows; the deadline must start at the send, so the probe still reads reachable
        def gate():
            self.clock.now += 25.0
            return True
        r = self._probe(send=lambda: ("200", b"ok"), gate=gate, deadline_s=20.0)
        self.assertEqual(r["state"], "reachable")

    def test_marks_round_trip_and_expire(self):
        cfg = Path(self.tmp.name)
        probe.save_mark(cfg, "nvidia-glm53", "unreachable", ttl_s=900.0, now=self.clock.now)
        marks = probe.load_marks(cfg, now=self.clock.now)
        self.assertIn("nvidia-glm53", marks)
        self.assertEqual(marks["nvidia-glm53"]["state"], "unreachable")
        later = probe.load_marks(cfg, now=self.clock.now + 901.0)
        self.assertNotIn("nvidia-glm53", later, "an expired mark must be dropped, never persisted")

    def test_marks_live_under_the_config_dir(self):
        cfg = Path(self.tmp.name)
        probe.save_mark(cfg, "a", "reachable", ttl_s=60.0, now=0.0)
        f = cfg / probe.MARKS_NAME
        self.assertTrue(f.exists())
        doc = json.loads(f.read_text())
        self.assertEqual(doc["a"]["state"], "reachable")


if __name__ == "__main__":
    unittest.main()
