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
NEW_ROUTES = {
    'mistral': ('MISTRAL_API_KEY', 'https://api.mistral.ai/v1'),
    'zai': ('ZAI_API_KEY', 'https://api.z.ai/api/paas/v4'),
    'siliconflow': ('SILICONFLOW_API_KEY', 'https://api.siliconflow.com/v1'),
    'llm7': ('LLM7_API_KEY', 'https://api.llm7.io/v1'),
    'kilo': ('KILO_API_KEY', 'https://api.kilo.ai/api/gateway'),
    'vercel': ('AI_GATEWAY_API_KEY', 'https://ai-gateway.vercel.sh/v1'),
    'sambanova': ('SAMBANOVA_API_KEY', 'https://api.sambanova.ai/v1'),
    'modelscope': ('MODELSCOPE_API_KEY', 'https://api-inference.modelscope.cn/v1'),
}


class RemoteSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in ('bin', 'config', 'keys', 'stubs', 'home'):
            (self.root / directory).mkdir()
        for rel in ('bin/csl', 'bin/remote-session.sh', 'bin/remote-keys.sh',
                    'config/remote-agents.sh', 'config/remote-agent-system-prompt.txt'):
            shutil.copy2(ROOT / rel, self.root / rel)
        source = (self.root / 'bin/remote-session.sh').read_text()
        (self.root / 'bin/library.sh').write_text(source.split('# ---- argument parsing')[0])
        for provider in ('gemini', 'groq', 'nvidia', 'openrouter', 'cerebras', 'cloudflare',
                         'github-models', *NEW_ROUTES):
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
        self.assertEqual(rows[0][0], 'gemini-flash')
        self.assertNotIn('github-models', [row[0] for row in rows])
        self.assertEqual(len(rows), len({row[0] for row in rows}))
        self.assertEqual({row[1] for row in rows}, {'gemini', 'groq', 'nvidia', 'openrouter', 'cloudflare', 'cerebras', *NEW_ROUTES})
        models = {row[0]: row[2] for row in rows}
        self.assertEqual(models['gemini-flash'], 'gemini-3.6-flash')
        self.assertEqual(models['gemini-3.8-flash'], 'gemini-3.8-flash')
        self.assertEqual(models['openrouter-free'], 'openrouter/free')
        for alias, provider, model, _, tier, _ in rows:
            with self.subTest(alias=alias):
                extra = ['--remote-model', 'fixture/model'] if model == 'SELECT' else []
                result = self.run_cli('remote', '--dry-run', '--include-trials', alias, *extra, csl=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('fixture/model' if extra else model, result.stdout)
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
                        openrouter='openrouter/', cerebras='cerebras/', cloudflare='openai/',
                        **{p: 'openai/' for p in NEW_ROUTES})
        for alias, provider, model, *_ in self.roster():
            if model == 'SELECT':
                model = 'fixture/model:version'
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
                if provider in NEW_ROUTES:
                    key_env, base = NEW_ROUTES[provider]
                    self.assertIn('api_base: ' + base, text)
                    self.assertIn('os.environ/' + key_env, text)

    def test_dynamic_models_validation_and_retired_github(self):
        for provider in NEW_ROUTES:
            with self.subTest(provider=provider):
                self.assertEqual(self.run_cli('--dry-run', provider).returncode, 2)
                good = self.run_cli('--dry-run', provider, '--remote-model', 'vendor/model:free')
                self.assertEqual(good.returncode, 0, good.stderr)
                self.assertIn('do not assume free', good.stdout)
                for invalid in ('', 'model\napi_key: evil', 'model with spaces', '-option'):
                    self.assertEqual(self.run_cli('--dry-run', provider, '--remote-model', invalid).returncode, 2)
                (self.root / 'keys' / provider).unlink()
                self.assertEqual(self.run_cli('--dry-run', provider, '--remote-model', 'vendor/model').returncode, 1)
        for args in (('--dry-run', 'github-models'), ('--verify', 'github-models'), ('--models', 'github-models')):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('retired', result.stderr)
        self.assertFalse((self.root / 'curl-argv').exists())
        key_list = subprocess.run(['bash', str(self.root / 'bin/remote-keys.sh'), '--list'],
                                  env=self.env, text=True, capture_output=True, check=True)
        self.assertNotIn('github', key_list.stdout.lower())
        self.assertTrue((self.root / 'keys/github-models').exists())
        overridden = self.run_cli('--dry-run', 'openrouter-free', '--remote-model', 'vendor/paid')
        self.assertEqual(overridden.returncode, 0)
        self.assertIn('do not assume free', overridden.stdout)
        self.assertNotIn('renewing free allocation', overridden.stdout)
        self.env['CATALOG'] = json.dumps({'data': [{'id': 'vendor/paid'}]})
        verified = self.run_cli('--verify', 'openrouter-free', '--remote-model', 'vendor/paid')
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn('tier       : unknown', verified.stdout)

    def test_dynamic_catalog_models_and_manual_only_routes(self):
        self.env['CATALOG'] = json.dumps({'data': [{'id': 'vendor/model'}, {'id': 'vendor/model2'}]})
        for provider in NEW_ROUTES.keys() - {'zai', 'modelscope'}:
            with self.subTest(provider=provider):
                result = self.run_cli('--models', provider)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('vendor/model2', result.stdout)
                argv = json.loads((self.root / 'curl-argv').read_text())
                endpoint = NEW_ROUTES[provider][1] + '/models'
                if provider == 'siliconflow':
                    endpoint += '?type=text'
                self.assertIn(endpoint, argv)
                verified = self.run_cli('--verify', provider, '--remote-model', 'vendor/model')
                self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        for provider in ('zai', 'modelscope'):
            result = self.run_cli('--models', provider)
            self.assertEqual(result.returncode, 2)
            self.assertIn('--remote-model', result.stderr)

    def test_catalog_prices_capabilities_and_invalid_records(self):
        self.env['CATALOG'] = json.dumps({'data': [
            {'id': 'free-model', 'pricing': {'prompt': '0', 'completion': '0'}, 'tools_calling': True},
            {'id': 'paid-model', 'tier': 'pro', 'pricing': {'input': 5}},
            {'id': 'image-model', 'model_type': 'image'},
            {'id': 'no-tools', 'capabilities': {'function_calling': False}},
            {'id': 'no-tool-parameter', 'supported_parameters': ['temperature']},
            {'id': 'embedding-model', 'type': 'embedding'},
        ]})
        result = self.run_cli('--models', 'llm7')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"prompt": "0"', result.stdout)
        self.assertIn('"tier": "pro"', result.stdout)
        self.assertIn('"tools": "unknown"', result.stdout)
        self.assertNotIn('image-model', result.stdout)
        self.assertNotIn('no-tools', result.stdout)
        self.assertNotIn('no-tool-parameter', result.stdout)
        self.assertNotIn('embedding-model', result.stdout)
        for payload in ({'error': 'bad'}, {'data': []}, {'data': [None]}, {'data': [{'id': 'bad\nmodel'}]}):
            self.env['CATALOG'] = json.dumps(payload)
            self.assertEqual(self.run_cli('--models', 'llm7').returncode, 1)

    def test_picker_requires_explicit_valid_selection(self):
        self.env['CATALOG'] = json.dumps({'data': [{'id': 'first/paid'}, {'id': 'second/free'}]})
        for selection, expected in (('2\n', 'second/free'), ('02\n', 'second/free'), ('\n', None), ('q\n', None), ('0\n', None), ('3\n', None)):
            with self.subTest(selection=selection):
                result = subprocess.run(['bash', '-c', 'source "$1"; choose_model llm7',
                                         'picker', str(self.root / 'bin/library.sh')],
                                        input=selection, env=self.env, text=True, capture_output=True)
                if expected:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)
                else:
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, '')
                self.assertIn('No model is selected by default', result.stderr)

    def test_manual_picker_without_catalog_for_zai_and_modelscope(self):
        for provider, domain in (('zai', 'docs.z.ai'), ('modelscope', 'modelscope.cn')):
            for selection, expected in (('vendor/model\n', 'vendor/model'), ('\n', None), ('q\n', None), ('bad model\n', None)):
                with self.subTest(provider=provider, selection=selection):
                    result = subprocess.run(['bash', '-c', 'source "$1"; choose_model "$2"',
                                             'picker', str(self.root / 'bin/library.sh'), provider],
                                            input=selection, env=self.env, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0 if expected else 2, result.stderr)
                    self.assertEqual(result.stdout, expected or '')
                    self.assertIn(domain, result.stderr)
        self.assertFalse((self.root / 'curl-argv').exists())

    def test_runtime_files_secure_on_reuse_and_reject_symlinks(self):
        runtime = self.root / 'local-agents-remote'
        runtime.mkdir()
        existing = runtime / 'proxy-4141.log'
        existing.write_text('old log')
        existing.chmod(0o644)
        result = subprocess.run(['bash', '-c', 'source "$1"; _prepare_runtime_file "$2"',
                                 'private', str(self.root / 'bin/library.sh'), str(existing)],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(existing.stat().st_mode & 0o777, 0o600)
        target = self.root / 'untouched'
        target.write_text('keep this')
        for name in ('proxy-4141.log', 'proxy-4141.yaml', 'proxy-4141.pid'):
            path = runtime / name
            path.unlink(missing_ok=True)
            path.symlink_to(target)
            result = subprocess.run(['bash', '-c', 'source "$1"; _prepare_runtime_file "$2"',
                                     'private', str(self.root / 'bin/library.sh'), str(path)],
                                    env=self.env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_text(), 'keep this')

    def test_new_routes_reach_claude_with_literal_scoped_proxy_keys(self):
        self.env['CHILD_ENV_PATH'] = str(self.root / 'child-env')
        self.env['CLAUDE_RESULT'] = str(self.root / 'claude-result')
        self.env['KEY_NAMES'] = ','.join([env for env, _ in NEW_ROUTES.values()] + ['GITHUB_MODELS_TOKEN', 'OPENAI_API_KEY'])
        for name in self.env['KEY_NAMES'].split(','):
            self.env[name] = 'unrelated-inherited-secret'
        self.stub('lsof', '#!/bin/sh\nif [ "${1:-}" = --help ]; then echo "Fixture all ports free; no env options"; exit 0; fi\nexit 1\n')
        self.stub('litellm', '''#!/usr/bin/env python3
import json,os,sys,time
if '--help' in sys.argv:
    print('Fixture proxy; CHILD_ENV_PATH and KEY_NAMES select environment capture.'); sys.exit(0)
with open(os.environ['CHILD_ENV_PATH'],'w') as f:
    json.dump({k:os.environ[k] for k in os.environ['KEY_NAMES'].split(',') if k in os.environ},f)
time.sleep(.3)
''')
        self.stub('claude', '''#!/usr/bin/env python3
import json,os,sys,time
if '--help' in sys.argv:
    print('Fixture Claude; CLAUDE_RESULT captures args and KEY_NAMES environment.'); sys.exit(0)
for _ in range(100):
    if os.path.exists(os.environ['CHILD_ENV_PATH']): break
    time.sleep(.01)
with open(os.environ['CLAUDE_RESULT'],'w') as f:
    json.dump({'argv':sys.argv[1:], 'base':os.environ['ANTHROPIC_BASE_URL'],
      'provider':os.environ['LA_REMOTE_PROVIDER'],
      'keys':{k:os.environ[k] for k in os.environ['KEY_NAMES'].split(',') if k in os.environ}},f)
''')
        marker = self.root / 'never-created'
        secret = 'literal$(touch ' + str(marker) + ') with spaces'
        for provider, (key_env, endpoint) in NEW_ROUTES.items():
            with self.subTest(provider=provider):
                capture = self.root / 'child-env'
                capture.unlink(missing_ok=True)
                (self.root / 'keys' / provider).write_text(secret)
                result = self.run_cli(provider, '--remote-model', 'fixture/model:free', '-p', 'fixture prompt')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(capture.read_text()), {key_env: secret})
                child = json.loads((self.root / 'claude-result').read_text())
                self.assertEqual(child['keys'], {})
                self.assertEqual(child['provider'], provider)
                self.assertTrue(child['base'].startswith('http://127.0.0.1:'))
                self.assertEqual(child['argv'][-2:], ['-p', 'fixture prompt'])
                cfg = (self.root / 'local-agents-remote/proxy-4141.yaml').read_text()
                self.assertIn('api_base: ' + endpoint, cfg)
                self.assertIn('model: openai/fixture/model:free', cfg)
                self.assertNotIn(secret, cfg + result.stdout + result.stderr)
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
    unittest.main()
