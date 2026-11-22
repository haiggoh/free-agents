#!/usr/bin/env python3
"""tests/test_lowkey_session_name.py — the session name set through csl's typed prompt
(prompt:session) must ride the CONVO launch argv (--session NAME), exactly as the TUI does.

In-process, pure model: the GUI's prompt:session dialog stores the name and injects it
on the LowkeyScreen at build (bin/fa_gui/app.py); these assertions pin the model half.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import session_picker_model as m  # noqa: E402


class LowkeySessionNameTests(unittest.TestCase):
    def _screen(self) -> "m.LowkeyScreen":
        s = m.Settings()
        agents = [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown")]
        scr = m.LowkeyScreen(s, api_agents=agents)
        scr.api_selection = agents[0]      # what activate_selected() sets on an API row pick
        return scr

    def test_one_shot_does_not_carry_it(self):
        # csl's contract: --session rides the CONVO launch only; one-shot is stateless
        scr = self._screen()
        scr.session_name = "my-run"
        req = scr.one_shot("do the thing")
        assert req is not None
        argv = req.argv
        self.assertIn("--prompt", argv)
        self.assertEqual(argv[argv.index("--prompt") + 1], "do the thing")
        self.assertNotIn("--session", argv)

    def test_convo_carries_session_name(self):
        scr = self._screen()
        scr.session_name = "convo-run"
        req = scr._start("convo")           # the "Start convo" row
        self.assertIsInstance(req, m.LaunchRequest)
        argv = req.argv
        self.assertIn("--convo", argv)
        self.assertIn("--session", argv)
        self.assertEqual(argv[argv.index("--session") + 1], "convo-run")

    def test_empty_name_is_ephemeral(self):
        scr = self._screen()
        req = scr.one_shot("no name")
        self.assertNotIn("--session", req.argv)


if __name__ == "__main__":
    unittest.main()