"""tests/test_remote_filters_toggle.py — toggling Remote filters > "Large context only" in csl.

0.62.0-0.63.3: RemoteFiltersScreen._put_remote_filter saved the value and then called
self._regroup(), a method only RemoteScreen has, so every toggle raised AttributeError right after
the save (proven 2026-11-25 by driving the row through run(), csl's Enter route). The Remote list
regroups from settings.remote_filters the next time it is shown.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_menu_state as sms  # noqa: E402
import session_picker_model as m  # noqa: E402


class LargeContextToggle(unittest.TestCase):
    def test_toggling_large_context_only_saves_and_does_not_raise(self):
        s = m.Settings(remote_filters={"large_context_only": True})
        scr = m.RemoteFiltersScreen(s)
        row = next(a for a in scr.actions() if a.key == "d")
        self.assertIn("Large context only", row.label)
        row.run()                                   # csl's Enter: must not raise
        self.assertIs(s.remote_filters["large_context_only"], False)
        self.assertIs(sms.load(sms.config_dir()).remote_filters.get("large_context_only"), False,
                      "the toggle must persist to the session-menu store")
        row = next(a for a in scr.actions() if a.key == "d")
        row.run()                                   # and back on
        self.assertIs(sms.load(sms.config_dir()).remote_filters.get("large_context_only"), True)


if __name__ == "__main__":
    unittest.main()
