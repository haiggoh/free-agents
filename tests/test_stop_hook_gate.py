#!/usr/bin/env python3
"""tests/test_stop_hook_gate.py — the queued-prompt Stop hook must fire ONLY for sessions
started by this project's launchers, and the csl `s` toggle must reach it.

WHAT THIS PROTECTS. The hook's whole job is to BLOCK the end of a turn. A gate that is too
loose therefore does not merely run somewhere unwanted — it hijacks turns in an ordinary paid
cloud session, which is the worst failure this repository can ship. So the gate is asserted
in both directions: it must ENGAGE for each known launcher, and it must NOT engage for a
plain `claude`, an unknown launcher name, or an inherited-but-empty marker.

WHY NOT THE OLD ENDPOINT CHECK. The predecessor asked "is ANTHROPIC_BASE_URL loopback?" as a
proxy for "did one of our launchers start this?" — a different question. It read a remote
session's LiteLLM proxy on 127.0.0.1 as local (right by accident), and would have enabled the
hook for any unrelated tool pointing Claude Code at localhost.

METHOD. The gate is exercised through a real subprocess with a controlled environment, and
asserted by OUTCOME: given a transcript containing an un-drained queued prompt, an engaged
hook prints a `decision: block` payload and a gated-out one prints nothing at all. Asserting
that a function returns False would not prove the hook stays silent.

Usage: python3 tests/test_stop_hook_gate.py [--help]
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'bin' / 'local-queue-stop-hook.py'


class StopHookGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # A transcript with an enqueue and NO drain -> an engaged hook must block on it.
        self.transcript = self.root / 'session.jsonl'
        self.transcript.write_text('\n'.join(json.dumps(r) for r in [
            {'type': 'user', 'message': {'role': 'user', 'content': 'first real turn'},
             'timestamp': '2026-09-16T01:00:00.000Z'},
            {'type': 'assistant', 'message': {'role': 'assistant',
                                              'content': [{'type': 'text', 'text': 'a real reply'}]},
             'timestamp': '2026-09-16T01:00:01.000Z'},
            {'type': 'queue-operation', 'operation': 'enqueue',
             'content': 'the prompt that must not be lost',
             'timestamp': '2026-09-16T01:00:02.000Z'},
        ]) + '\n')

    def run_hook(self, **env):
        payload = json.dumps({'transcript_path': str(self.transcript), 'stop_hook_active': False})
        base = {k: v for k, v in os.environ.items()
                if k not in ('LA_SESSION_LAUNCHER', 'LA_QUEUE_STOP_HOOK', 'ANTHROPIC_BASE_URL')}
        base.update(env)
        return subprocess.run([sys.executable, str(HOOK)], input=payload,
                              text=True, capture_output=True, env=base)

    def assertBlocks(self, result, msg):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip(), msg + ' (expected a block payload, got nothing)')
        self.assertEqual(json.loads(result.stdout)['decision'], 'block', msg)

    def assertSilent(self, result, msg):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '', msg + ' (hook spoke when it should be gated out)')

    def test_engages_for_every_known_launcher(self):
        for launcher in ('csl', 'launch-claude-agent.sh', 'remote-session.sh'):
            with self.subTest(launcher=launcher):
                self.assertBlocks(self.run_hook(LA_SESSION_LAUNCHER=launcher),
                                  'the hook must engage for ' + launcher)

    def test_never_engages_for_an_ordinary_cloud_session(self):
        """THE CRITICAL DIRECTION: a plain gateway `claude` must be untouched."""
        self.assertSilent(self.run_hook(), 'no marker at all (plain `claude`)')
        self.assertSilent(self.run_hook(ANTHROPIC_BASE_URL='https://llmgw.joyia.p7s1.io'),
                          'a gateway endpoint with no launcher marker')
        # An inherited-but-empty marker is the leak shape: a launcher exported it, a later
        # gateway claude in the same shell inherits it. Empty must NOT open the gate.
        self.assertSilent(self.run_hook(LA_SESSION_LAUNCHER=''), 'empty inherited marker')
        self.assertSilent(self.run_hook(LA_SESSION_LAUNCHER='some-other-tool'),
                          'an unrecognised launcher name')

    def test_a_loopback_endpoint_alone_does_not_open_the_gate(self):
        """Regression against the predecessor's predicate.

        The old gate would have engaged here purely because the endpoint is loopback, even
        though no launcher of ours started the session.
        """
        self.assertSilent(self.run_hook(ANTHROPIC_BASE_URL='http://localhost:8000'),
                          'loopback endpoint without a launcher marker')

    def test_explicit_override_wins_in_both_directions(self):
        self.assertSilent(self.run_hook(LA_SESSION_LAUNCHER='csl', LA_QUEUE_STOP_HOOK='0'),
                          'LA_QUEUE_STOP_HOOK=0 must force the hook OFF (the csl `s` toggle)')
        self.assertBlocks(self.run_hook(LA_QUEUE_STOP_HOOK='1'),
                          'LA_QUEUE_STOP_HOOK=1 must force it ON even with no launcher marker')

    def test_help_prints_and_does_not_run(self):
        """A --help probe must not execute the hook (global rule: every script has --help)."""
        result = subprocess.run([sys.executable, str(HOOK), '--help'],
                                text=True, capture_output=True, input='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Usage:', result.stdout)
        self.assertIn('LA_SESSION_LAUNCHER', result.stdout)
        # Not `assertNotIn('decision', ...)`: the help text legitimately DOCUMENTS the
        # decision payload. Assert the output is not itself a payload — help must describe
        # the behaviour without performing it.
        self.assertFalse(result.stdout.lstrip().startswith('{'),
                         'help output must not be a JSON decision payload')
        bad = subprocess.run([sys.executable, str(HOOK), '--bogus'],
                             text=True, capture_output=True, input='')
        self.assertEqual(bad.returncode, 2, 'an unknown flag must exit non-zero')

    def test_csl_and_launchers_actually_stamp_the_marker(self):
        """The gate is worthless if no launcher sets the value it tests for."""
        launcher = (ROOT / 'bin/launch-claude-agent.sh').read_text()
        self.assertIn('LA_SESSION_LAUNCHER="launch-claude-agent.sh"', launcher)
        self.assertIn('LA_QUEUE_STOP_HOOK', launcher)
        remote = (ROOT / 'bin/remote-session.sh').read_text()
        self.assertIn('LA_SESSION_LAUNCHER="remote-session.sh"', remote)
        csl = (ROOT / 'bin/csl').read_text()
        self.assertIn('LA_QUEUE_STOP_HOOK="$STOP_HOOK"', csl)
        self.assertIn('s|S)', csl, 'the `s` key must toggle the hook')


if __name__ == '__main__':
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
        sys.exit(0)
    unittest.main()
