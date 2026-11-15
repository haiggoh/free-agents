#!/usr/bin/env python3
"""CLI-surface tests for bin/launch-local-auto-mode.sh (waypoint launch-local-auto-mode-sh-cli).

Defect: the exec-only guard rejected ANY positional $1 (exit 2), while the usage comment and
the launch-claude-agent.sh --auto dispatch (exec ... "${@:2}") pass <main-alias> [a|b] [effort]
— so direct execution could never launch. Contract after the fix: --help runs nothing (exit 0);
unknown -flags exit 2 with usage; bare positional args pass through to the existing
alias/strategy validation (unknown alias exits 1 loudly); no-arg without the opt-in still
prints the OFF message and exits 0 without launching.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "launch-local-auto-mode.sh"

CLAUDE_STUB = '''#!/usr/bin/env python3
import json, os, sys
if '--help' in sys.argv:
    print('Fixture Claude'); sys.exit(0)
if '--version' in sys.argv:
    print('2.1.286'); sys.exit(0)
with open(os.environ['CLAUDE_ARGV'], 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
'''


class AutoModeCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = Path(self.tmp.name)
        for d in ('bin', 'config', 'stubs', 'home', 'live', 'cfgdir', 'target'):
            (r / d).mkdir()
        for rel in ('bin/launch-local-auto-mode.sh', 'bin/fa_pre_trust.py',
                    'config/config-lib.sh', 'config/emoji.sh'):
            shutil.copy2(ROOT / rel, r / rel)
        (r / 'bin' / 'launch-local-auto-mode.sh').chmod(0o755)
        (r / 'live' / 'config.local.sh').write_text(
            'la_register testmodel models/testmodel rapid "" "" "" "claude-opus-5" medium\n')
        (r / 'bin' / 'local-llm-hotswap.sh').write_text('#!/bin/sh\necho "SUCCESS_PORT=8000"\n')
        (r / 'bin' / 'local-llm-hotswap.sh').chmod(0o755)
        (r / 'cfgdir' / '.claude.json').write_text('{"projects": {}}\n')

        def stub(name, content):
            p = r / 'stubs' / name
            p.write_text(content)
            p.chmod(0o755)

        stub('curl', '#!/bin/sh\necho \'{"id":"claude-opus-5"}\'\n')
        stub('lsof', '#!/bin/sh\nif [ "${1:-}" = --help ]; then echo "Fixture: all ports free"; exit 0; fi\nexit 1\n')
        stub('claude', CLAUDE_STUB)
        self.root = r
        self.env = dict(os.environ, HOME=str(r / 'home'), LA_LIVE_CONFIG_DIR=str(r / 'live'),
                        CLAUDE_CONFIG_DIR=str(r / 'cfgdir'), JOYIA_LOCAL_AUTO_CLASSIFIER='1',
                        PATH=str(r / 'stubs') + ':' + os.environ['PATH'],
                        CLAUDE_ARGV=str(r / 'claude-argv'))
        self.env.pop('_LA_LIVE_FOR', None)

    def run_cli(self, *args, opt_in=True):
        env = dict(self.env)
        if not opt_in:
            env.pop('JOYIA_LOCAL_AUTO_CLASSIFIER', None)
        return subprocess.run(['bash', str(self.root / 'bin/launch-local-auto-mode.sh'), *args],
                              env=env, cwd=str(self.root / 'target'), stdin=subprocess.DEVNULL,
                              text=True, capture_output=True, timeout=120)

    def launched(self):
        return (self.root / 'claude-argv').exists()

    def test_help_exits_0_and_launches_nothing(self):
        r = self.run_cli('--help')
        self.assertEqual(r.returncode, 0)
        self.assertIn('usage', r.stdout.lower())
        self.assertFalse(self.launched(), '--help must never start a session')

    def test_unknown_flag_exits_2_with_usage(self):
        r = self.run_cli('--bogus')
        self.assertEqual(r.returncode, 2)
        self.assertIn('usage', r.stderr.lower())
        self.assertFalse(self.launched())

    def test_positional_args_reach_the_launch(self):
        # the documented form: <main-alias> [strategy a|b] — must NOT be rejected by the
        # CLI guard (waypoint launch-local-auto-mode-sh-cli)
        r = self.run_cli('testmodel', 'a')
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertTrue(self.launched(),
                        'claude was never launched with positional args: ' + r.stdout[-1500:] + r.stderr[-1500:])

    def test_unknown_alias_fails_loudly_not_with_usage(self):
        r = self.run_cli('nosuchalias')
        self.assertEqual(r.returncode, 1)
        self.assertIn('unknown main-alias', r.stdout + r.stderr)
        self.assertFalse(self.launched())

    def test_no_args_without_opt_in_still_off(self):
        r = self.run_cli(opt_in=False)
        self.assertEqual(r.returncode, 0)
        self.assertIn('OFF by design', r.stdout)
        self.assertFalse(self.launched())


if __name__ == '__main__':
    unittest.main()
