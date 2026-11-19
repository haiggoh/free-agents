#!/usr/bin/env python3
"""tests/test_session_preferences_sync.py — 0.59.9 pin: config/session-preferences.json 'order'
(the full-SESSION model ladder) must only name aliases the live roster still offers and that are
not retired/broken. Rule: "a retired id must never be in a fallback chain" (0.59.8, deepseek-v4.1-
flash 410 EOL 2026-11-16) — extended 2026-11-19 to the session ladder after a session launcher
put a quota-limited model on top of a full interactive session.

Also pinned: dispatch-only providers (Gemini — fine for stateless dispatches, never for full
sessions; the quota is the problem, not the model) never appear in the session order.

Usage: python3 tests/test_session_preferences_sync.py [-v]   (or pytest)
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def roster_aliases():
    """Aliases from the generated config/remote-agents.sh LA_REMOTE_AGENTS block."""
    text = (ROOT / 'config' / 'remote-agents.sh').read_text()
    return set(re.findall(r'^\s*"([a-z0-9-]+)\|', text, re.MULTILINE))


def broken_aliases():
    data = json.loads((ROOT / 'config' / 'broken-nvidia-models.json').read_text())
    return {m['alias'] for m in data.get('models', [])
            if m.get('status', 'broken') == 'broken' and m.get('alias')}


class SessionPreferencesSyncTests(unittest.TestCase):
    def _prefs(self):
        return json.loads((ROOT / 'config' / 'session-preferences.json').read_text())

    def test_session_order_aliases_exist_in_roster_and_are_not_retired(self):
        order = self._prefs()['order']
        self.assertTrue(order, 'the session order must not be empty')
        roster, broken = roster_aliases(), broken_aliases()
        missing = [a for a in order if a not in roster]
        retired = [a for a in order if a in broken]
        self.assertEqual(missing, [], f'session-order aliases absent from the roster: {missing}')
        self.assertEqual(retired, [], f'retired ids must never be in a session chain: {retired}')

    def test_no_dispatch_only_provider_leads_or_fills_a_session(self):
        """Gemini aliases are dispatch-only (quota burns in minutes inside a full session,
        user 2026-11-19): none may appear in the full-session order at all."""
        order = self._prefs()['order']
        gemini = [a for a in order if a.startswith('gemini')]
        self.assertEqual(gemini, [], f'dispatch-only models must not be in the session order: {gemini}')


if __name__ == '__main__':
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
    unittest.main()
