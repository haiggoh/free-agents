#!/usr/bin/env python3
"""Offline tests for remote session routing; never call real providers.

Usage: python3 tests/test_remote_session.py [-v] [test names]
Environment: none required; HOME, PATH and credential paths are isolated.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RemoteSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in ('bin', 'config', 'keys', 'stubs', 'home'):
            (self.root / directory).mkdir()
        for rel in ('bin/csl', 'bin/remote-session.sh', 'bin/remote-keys.sh',
                    'config/remote-agents.sh'):
            shutil.copy2(ROOT / rel, self.root / rel)
        source = (self.root / 'bin/remote-session.sh').read_text()
        (self.root / 'bin/library.sh').write_text(source.split('# ---- argument parsing')[0])
        for provider in ('gemini', 'groq', 'nvidia', 'openrouter', 'cerebras', 'cloudflare'):
            (self.root / 'keys' / provider).write_text('fixture-not-a-real-key')
        (self.root / 'keys/cloudflare-account-id').write_text('a' * 32)
        self.env = dict(os.environ, HOME=str(self.root / 'home'),
                        TMPDIR=str(self.root), LA_API_KEYS_DIR=str(self.root / 'keys'),
                        PATH=str(self.root / 'stubs') + ':' + os.environ['PATH'],
                        CATALOG=json.dumps({'data': []}), CURL_CODE='0',
                        CURL_ARGV=str(self.root / 'curl-argv'))
        self.stub('curl', '''#!/usr/bin/env python3
import json,os,sys
if '--help' in sys.argv:
    print('Offline catalog fixture; CATALOG and CURL_CODE control its response.'); sys.exit(0)
open(os.environ['CURL_ARGV'],'w').write(json.dumps(sys.argv[1:]))
sys.stdin.read()
print(os.environ['CATALOG'])
sys.exit(int(os.environ['CURL_CODE']))
''')
        for name in ('litellm', 'claude'):
            self.stub(name, '#!/bin/sh\nif [ "${1:-}" = --help ]; then echo "Offline forbidden-launch sentinel"; exit 0; fi\necho "Unexpected launch" >&2\nexit 99\n')

    def stub(self, name, content):
        path = self.root / 'stubs' / name
        path.write_text(content)
        path.chmod(0o755)
        return path

    def run_cli(self, *args, csl=False):
        return subprocess.run(['bash', str(self.root / ('bin/csl' if csl else 'bin/remote-session.sh')), *args],
                              env=self.env, text=True, capture_output=True)

    def roster(self):
        result = subprocess.run(['bash', '-c', 'source "$1"; printf "%s\\n" "${LA_REMOTE_AGENTS[@]}"',
                                 'roster', str(self.root / 'config/remote-agents.sh')],
                                env=self.env, text=True, capture_output=True, check=True)
        return [row.split('|') for row in result.stdout.splitlines()]

    def test_roster_and_all_provider_dry_runs(self):
        rows = self.roster()
        self.assertEqual(len(rows), len({row[0] for row in rows}))
        self.assertEqual({row[1] for row in rows}, {'gemini', 'groq', 'nvidia', 'openrouter', 'cloudflare', 'cerebras'})
        models = {row[0]: row[2] for row in rows}
        self.assertEqual(models['gemini-flash'], 'gemini-3.6-flash')
        self.assertEqual(models['gemini-3.8-flash'], 'gemini-3.8-flash')
        self.assertEqual(models['openrouter-free'], 'openrouter/free')
        for alias, provider, model, _, tier, _ in rows:
            with self.subTest(alias=alias):
                result = self.run_cli('remote', '--dry-run', '--include-trials', alias, csl=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(model, result.stdout)
                self.assertIn('no network call made', result.stdout)
        self.assertFalse((self.root / 'curl-argv').exists())

    def test_direct_remote_entry_without_local_config(self):
        result = self.run_cli('remote', '--list', '--include-trials', csl=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for provider in ('Gemini 3.8', 'Gemini 3.6', 'Cloudflare', 'Cerebras'):
            self.assertIn(provider, result.stdout)
        self.assertFalse((self.root / 'local-agents-remote').exists())

    def test_help_unknown_flags_and_missing_key(self):
        self.assertEqual(self.run_cli('--help', csl=True).returncode, 0)
        for args in (('--help',), ('--bad-option',), ('--verify',)):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0 if args == ('--help',) else 2, result.stderr)
        (self.root / 'keys/gemini').unlink()
        self.assertEqual(self.run_cli('--dry-run', 'gemini-3.8-flash').returncode, 1)
        self.assertFalse((self.root / 'curl-argv').exists())
        self.assertFalse((self.root / 'local-agents-remote').exists())

    def test_proxy_receives_literal_credentials(self):
        marker = self.root / 'must-not-exist'
        secret = 'fixture$(touch ' + str(marker) + ') with spaces'
        (self.root / 'keys/cloudflare').write_text(secret)
        self.env['CHILD_ENV_PATH'] = str(self.root / 'child-env')
        self.stub('lsof', '#!/bin/sh\nif [ "${1:-}" = --help ]; then echo "Fixture: all ports free; no environment options"; exit 0; fi\nexit 1\n')
        self.stub('litellm', '''#!/usr/bin/env python3
import json,os,sys
if '--help' in sys.argv:
    print('Fixture proxy; writes selected credential environment to CHILD_ENV_PATH.'); sys.exit(0)
with open(os.environ['CHILD_ENV_PATH'],'w') as f:
    json.dump({k:os.environ.get(k) for k in ('CLOUDFLARE_API_TOKEN','CLOUDFLARE_ACCOUNT_ID')},f)
''')
        result = subprocess.run(['bash', '-c',
                                 'source "$1"; mkdir -p "$RUNDIR"; start_proxy cloudflare @cf/openai/gpt-oss-20b false; wait',
                                 'proxy', str(self.root / 'bin/library.sh')],
                                env=self.env, stdin=subprocess.DEVNULL, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        received = json.loads((self.root / 'child-env').read_text())
        self.assertEqual(received['CLOUDFLARE_API_TOKEN'], secret)
        self.assertEqual(received['CLOUDFLARE_ACCOUNT_ID'], 'a' * 32)
        self.assertFalse(marker.exists())

    def test_trial_opt_in_and_legacy_alias(self):
        self.assertNotIn('cerebras-oss120', self.run_cli('--list').stdout)
        self.assertEqual(self.run_cli('--dry-run', 'cerebras-oss120').returncode, 2)
        result = self.run_cli('--dry-run', '--include-trials', 'cerebras-legacy')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('gpt-oss-120b', result.stdout)
        self.assertIn('trial/paid access explicitly selected', result.stdout)
        self.assertIn('old Llama id is unavailable', result.stderr)
        self.assertIn('do not assume free', self.run_cli('--dry-run', 'cloudflare-oss20').stdout)

    def test_catalog_shapes_errors_and_no_key_in_argv(self):
        cases = [
            ('gemini-3.8-flash', {'data': [{'id': 'models/gemini-3.8-flash'}]}, 0),
            ('cloudflare-oss20', {'success': True, 'result': [{'id': 'uuid', 'name': '@cf/openai/gpt-oss-20b'}]}, 0),
            ('gemini-3.8-flash', {'data': []}, 3),
            ('gemini-3.8-flash', {'error': 'unauthorized'}, 1),
            ('cloudflare-oss20', {'success': False, 'result': []}, 1),
        ]
        for alias, payload, code in cases:
            with self.subTest(alias=alias, payload=payload):
                self.env['CATALOG'] = json.dumps(payload)
                result = self.run_cli('--verify', alias)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                argv = (self.root / 'curl-argv').read_text()
                self.assertNotIn('fixture-not-a-real-key', argv)
                self.assertIn('--config', argv)
                if code == 0:
                    self.assertIn('generation and quota untested', result.stdout)
        self.env['CURL_CODE'] = '22'
        self.assertEqual(self.run_cli('--verify', 'gemini-3.8-flash').returncode, 1)
        self.env['CURL_CODE'] = '0'
        self.env['CATALOG'] = 'not json'
        self.assertEqual(self.run_cli('--verify', 'gemini-3.8-flash').returncode, 1)

    def test_proxy_config_all_models_and_thinking(self):
        prefixes = dict(gemini='gemini/', groq='groq/', nvidia='nvidia_nim/',
                        openrouter='openrouter/', cerebras='cerebras/', cloudflare='openai/')
        for alias, provider, model, *_ in self.roster():
            with self.subTest(alias=alias):
                cfg = self.root / 'proxy.yaml'
                thinking = 'true' if alias.endswith('-thinking') else 'false'
                result = subprocess.run(['bash', '-c', 'source "$1"; write_proxy_config "$2" "$3" "$4" "$5"',
                                         'config', str(self.root / 'bin/library.sh'), str(cfg), provider, model, thinking],
                                        env=self.env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                text = cfg.read_text()
                self.assertEqual(text.count('      model: ' + prefixes[provider] + model + '\n'), 4)
                self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
                self.assertNotIn('fixture-not-a-real-key', text)
                self.assertEqual('thinking:' in text, provider == 'gemini' and thinking == 'false')
                if provider == 'cloudflare':
                    self.assertIn('/accounts/' + 'a' * 32 + '/ai/v1', text)
                    self.assertIn('os.environ/CLOUDFLARE_API_TOKEN', text)


if __name__ == '__main__':
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
    unittest.main()
