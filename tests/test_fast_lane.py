"""0.63.0 — opt-in fast lane for agents/offloads in the rate limiter (waypoint
fast-lane-for-agents-offloads; spec ~/.claude/plans/2026-11-23-fast-lane-for-agents-offloads-...).

Setting `fast_lane` / LA_LIMITER_FAST_LANE: off (default, = 0.62.3 behavior), agents
(dispatch + agent rank above interactive), all-noninteractive (also background).
Rank tuple becomes (class, fast, tier, boost, -arrived): a fast-lane request beats any
non-priority interactive regardless of age tier; the manual priority lane still wins
(cls=1); while normal waiters queue, fast+priority grants share the lane share cap.
"""
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import rate_limiter as rl  # noqa: E402

BASE_CFG = {"policy": "aging", "tier1_seconds": 45.0, "tier2_seconds": 150.0,
            "lane_share_cap": 0.2, "dispatch_boost": True, "fast_lane": "off"}


def _w(kind, arrived, cls="normal", uid=""):
    return {"owner": f"o-{uid or kind}-{arrived}", "arrived": arrived, "kind": kind, "class": cls}


class FastLaneSettingTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(rl._fast_lane_kinds({"fast_lane": "off"}), frozenset())
        self.assertEqual(rl._fast_lane_kinds({"fast_lane": "agents"}),
                         frozenset({"dispatch", "agent"}))
        self.assertEqual(rl._fast_lane_kinds({"fast_lane": "all-noninteractive"}),
                         frozenset({"dispatch", "agent", "background"}))
        self.assertEqual(rl._fast_lane_kinds({"fast_lane": "bogus"}),
                         frozenset({"dispatch", "agent"}), "unknown value -> agents arm")

    def test_agent_kind_accepted(self):
        with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_KIND": "agent"}):
            self.assertEqual(rl._kind(), "agent")
        with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_KIND": "nonsense"}):
            self.assertEqual(rl._kind(), "interactive", "unknown kind falls back to interactive")


class RankTests(unittest.TestCase):
    NOW = 10_000.0

    def test_off_is_0623_behavior(self):
        cfg = dict(BASE_CFG)                      # fast_lane off
        w = _w("dispatch", self.NOW)
        self.assertEqual(rl._rank(w, self.NOW, cfg), (0, 0, 0, 1, -self.NOW))

    def test_fast_lane_beats_aged_interactive(self):
        cfg = dict(BASE_CFG, fast_lane="agents")
        fresh_dispatch = _w("dispatch", self.NOW)                 # tier 0
        aged_interactive = _w("interactive", self.NOW - 200.0)     # tier 2 (150s)
        self.assertGreater(rl._rank(fresh_dispatch, self.NOW, cfg),
                           rl._rank(aged_interactive, self.NOW, cfg),
                           "fast lane: fresh dispatch must outrank an aged interactive")
        cfg_off = dict(BASE_CFG)
        self.assertGreater(rl._rank(aged_interactive, self.NOW, cfg_off),
                           rl._rank(fresh_dispatch, self.NOW, cfg_off),
                           "with fast_lane off the aged interactive wins (0.62.3 behavior)")

    def test_priority_lane_still_beats_fast_lane(self):
        cfg = dict(BASE_CFG, fast_lane="agents")
        fast = _w("dispatch", self.NOW)
        prio = _w("interactive", self.NOW, cls="priority")
        self.assertGreater(rl._rank(prio, self.NOW, cfg), rl._rank(fast, self.NOW, cfg))


class LaneCapTests(unittest.TestCase):
    NOW = 10_000.0

    def _winner(self, waiters, cfg, prio_grants, owners=None):
        return rl._pick_winner(waiters, self.NOW, cfg, owners or {}, {}, 40, prio_grants)

    def test_cap_covers_fast_and_priority_when_normal_waiters_queue(self):
        cfg = dict(BASE_CFG, fast_lane="agents")
        waiters = {"t1": _w("dispatch", self.NOW, uid="fast"),
                   "t2": _w("interactive", self.NOW, uid="prio"),   # priority via owners map
                   "t3": _w("interactive", self.NOW, uid="norm")}
        owners = {"o-prio-10000.0": {"priority": True}}
        cap = rl._lane_cap(40, cfg["lane_share_cap"])               # 8
        w = self._winner(waiters, cfg, cap, owners)
        self.assertEqual(w, "t3",
                         "lane cap reached: both priority and fast waiters wait; normal wins")

    def test_off_fast_lane_ignores_cap_for_dispatch(self):
        cfg = dict(BASE_CFG)                                        # off
        waiters = {"t1": _w("dispatch", self.NOW, uid="fast"),
                   "t2": _w("interactive", self.NOW, uid="prio"),
                   "t3": _w("interactive", self.NOW, uid="norm")}
        owners = {"o-prio-10000.0": {"priority": True}}
        cap = rl._lane_cap(40, cfg["lane_share_cap"])
        w = self._winner(waiters, cfg, cap, owners)
        self.assertEqual(w, "t1", "with fast_lane off only priority is capped; dispatch wins")

    def test_cap_does_not_bind_without_normal_waiters(self):
        cfg = dict(BASE_CFG, fast_lane="agents")
        waiters = {"t1": _w("dispatch", self.NOW, uid="fast"),
                   "t2": _w("interactive", self.NOW, uid="prio")}
        owners = {"o-prio-10000.0": {"priority": True}}
        cap = rl._lane_cap(40, cfg["lane_share_cap"])
        w = self._winner(waiters, cfg, cap, owners)
        self.assertEqual(w, "t2", "no normal waiter -> cap does not bind; priority wins")


if __name__ == "__main__":
    unittest.main()


class _FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class KindPlumbingTests(unittest.TestCase):
    """0.63.0 task 2: the per-request kind reaches the waiter entry."""

    def test_agent_kind_registers_on_the_waiter(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_KIND": "interactive",
                                                   "LA_NVIDIA_THROTTLE_STATE":
                                                       os.path.join(tmp, "s")}):
            clock = _FakeClock()
            lim = rl.SlidingWindowLimiter(clock=clock)
            for _ in range(40):
                self.assertEqual(lim.try_acquire(), 0.0)      # fill the window
            wait = lim.try_acquire(token="tk-agent", kind="agent")
            self.assertGreater(wait, 0.0, "window full: registers and waits")
            kinds = [w["kind"] for w in lim.status()["waiters"]]
            self.assertIn("agent", kinds, f"waiter must carry kind=agent, got {kinds}")
