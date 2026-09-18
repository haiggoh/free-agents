#!/usr/bin/env python3
"""Offline tests for remote session routing; never call real providers.

Usage: python3 tests/test_remote_session.py [-v] [test names]
Environment: none required; HOME, PATH and credential paths are isolated.
"""
import itertools
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
                    'config/remote-agents.sh', 'config/remote-agent-system-prompt.txt',
                    'config/shared-agent-shipping-rules.txt'):
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
                        CURL_ARGV=str(self.root / 'curl-argv'),
                        # The stubs are /bin/sh scripts, so the launcher's real path
                        # (resolve the pipx interpreter, re-enter it without -E through
                        # the trust wrapper) cannot apply to them. Point the launcher at
                        # the stub as a complete command instead. The trust-wrapper path
                        # itself is covered by tests/test_litellm_trust_shim.py.
                        LA_LITELLM_CMD=str(self.root / 'stubs' / 'litellm'))
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
        # ROW 1 IS THE ENTER-DEFAULT in every picker that reads this roster in order, so the
        # first row is a deliberate product decision and is pinned as such. NVIDIA leads
        # because its limits are generous with no known DAILY quota (the constraint that
        # ends a Gemini working session), and because real sessions run on it. Changing
        # this line should mean changing the preferred lane on purpose -- not drifting into it.
        self.assertEqual(rows[0][0], 'nvidia-nemotron3')
        self.assertEqual(rows[0][1], 'nvidia')
        # NVIDIA occupies the whole leading block; Gemini follows as tier 2 rather than vanishing.
        leading = list(itertools.takewhile(lambda r: r[1] == 'nvidia', rows))
        self.assertGreaterEqual(len(leading), 12, 'the NVIDIA tier-1 block should lead the roster')
        self.assertEqual(rows[len(leading)][1], 'gemini', 'Gemini should immediately follow the NVIDIA block')
        # Kimi K3 was requested by name; assert the id, not merely that some kimi row exists.
        self.assertEqual({r[0]: r[2] for r in rows}['nvidia-kimi-k3'], 'moonshotai/kimi-k3')
        # (Reasoning-disabled-for-every-nvidia-row is asserted in
        # test_proxy_config_all_models_and_thinking, which already walks the whole roster.)
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
                self.assertEqual('      thinking:' in text, provider == 'gemini' and thinking == 'false')
                # NVIDIA NIM reasoning models must have reasoning disabled at the
                # backend, or the Anthropic translation layer 500s the session.
                self.assertEqual(text.count('        enable_thinking: false\n'),
                                 4 if provider == 'nvidia' and thinking == 'false' else 0)
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

    def test_interactive_toggles_are_applied_not_merely_parsed(self):
        """Every advertised toggle must change the RESOLVED launch state.

        REGRESSION GUARD (the bug this replaces). `feat: enhance remote-session.sh to match
        csl interactive capabilities` mapped the toggles inside a `_launch()` helper that was
        NEVER CALLED, and the live path hardcoded its own values. So `-a` and `-t` parsed
        cleanly, printed nothing, and did nothing: 4 of 6 advertised switches were dead.
        A test that only asserted "the flag is accepted" passed throughout.

        Therefore assert on the RESOLVED OUTCOME (the permission-mode/telemetry the session
        would actually run with), never on parse success. Each case below fails if the
        mapping is removed, which is what makes it a guard rather than decoration.
        """
        # THE STRONG ASSERTION: capture what the real `claude` process is invoked with.
        # A dry-run print is NOT sufficient evidence -- verified by mutation: deleting the
        # `claude_cmd+=(--permission-mode ...)` line left every dry-run assertion passing,
        # because the summary and the command are built separately. Only the child's argv
        # proves the flag reaches the session. Stubs mirror
        # test_new_routes_reach_claude_with_literal_scoped_proxy_keys: lsof reports every
        # port free and litellm exits immediately, otherwise the launch waits on a proxy
        # that never becomes ready and the test hangs instead of failing.
        self.stub('lsof', '#!/bin/sh\nif [ "${1:-}" = --help ]; then echo "Fixture: all ports free"; exit 0; fi\nexit 1\n')
        self.stub('litellm', '#!/bin/sh\nif [ "${1:-}" = --help ]; then echo "Fixture proxy"; exit 0; fi\nsleep 2\n')
        self.env['CLAUDE_ARGV'] = str(self.root / 'claude-argv')
        self.stub('claude', """#!/usr/bin/env python3
import json,os,sys
if '--help' in sys.argv:
    print('Fixture Claude; CLAUDE_ARGV captures argv.'); sys.exit(0)
json.dump(sys.argv[1:], open(os.environ['CLAUDE_ARGV'],'w'))
""")

        def launched_argv(*args):
            capture = self.root / 'claude-argv'
            capture.unlink(missing_ok=True)
            result = self.run_cli(*args)
            self.assertTrue(capture.exists(),
                            'claude was never launched: ' + result.stdout + result.stderr)
            return json.loads(capture.read_text())

        argv = launched_argv('gemini-flash')
        self.assertIn('--permission-mode', argv)
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'auto',
                         'blind-trust must be the DEFAULT permission mode for remote')

        argv = launched_argv('-a', '-a', 'gemini-flash')
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'acceptEdits',
                         'two `-a` presses must reach the OFF state on the REAL command')

        # Default: blind-trust. The user's stated requirement -- acceptEdits is too
        # cumbersome to use -- so a regression to acceptEdits-by-default must fail here.
        default = self.run_cli('--dry-run', 'gemini-flash')
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertIn('--permission-mode auto', default.stdout)
        self.assertIn('blind-trust', default.stdout)
        self.assertIn('telemetry      : OFF', default.stdout)

        # `-a` once = classifier. The lane is not implemented for remote, so it must SAY so
        # and fall back to auto -- silently behaving like blind-trust is the dead-switch bug.
        once = self.run_cli('--dry-run', '-a', 'gemini-flash')
        self.assertEqual(once.returncode, 0, once.stderr)
        self.assertIn('--permission-mode auto', once.stdout)
        self.assertIn('not', once.stdout + once.stderr)
        self.assertIn('classifier', once.stdout + once.stderr)

        # `-a` twice = off -> acceptEdits. Distinct from the other two states.
        twice = self.run_cli('--dry-run', '-a', '-a', 'gemini-flash')
        self.assertEqual(twice.returncode, 0, twice.stderr)
        self.assertIn('--permission-mode acceptEdits', twice.stdout)

        # `-a` three times wraps back to blind-trust (the cycle must be a cycle).
        thrice = self.run_cli('--dry-run', '-a', '-a', '-a', 'gemini-flash')
        self.assertEqual(thrice.returncode, 0, thrice.stderr)
        self.assertIn('--permission-mode auto', thrice.stdout)
        self.assertIn('blind-trust', thrice.stdout)

        # Telemetry must work in BOTH directions. The live path used to hardcode the
        # suppression, so `-t` could never restore stock behaviour -- assert the ON state.
        tel = self.run_cli('--dry-run', '-t', 'gemini-flash')
        self.assertEqual(tel.returncode, 0, tel.stderr)
        self.assertIn('telemetry      : ON', tel.stdout)

    def test_watcher_flag_refuses_instead_of_silently_doing_nothing(self):
        """`-w` is deferred for remote, so it must FAIL LOUDLY rather than no-op.

        The old code accepted `-w` and printed "not fully implemented", then launched
        normally -- an advertised switch that did nothing. Deferring a feature is fine;
        pretending to have it is not. csl must also stop forwarding it.
        """
        result = self.run_cli('-w', 'gemini-flash')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('watcher', result.stderr)
        self.assertNotIn('-w, --watcher', self.run_cli('--help').stdout)
        # csl must not forward -w into remote (that would make the launch exit 2).
        self.assertNotIn('remote_args+=("-w")', (self.root / 'bin/csl').read_text())

    def test_local_capable_shown_flag_reaches_the_filter(self):
        """`--local-capable-shown` must actually change what the roster table shows.

        REGRESSION GUARD for the same class of bug as the auto-mode/telemetry dead-switch
        test above: a flag that parses cleanly but never reaches the code that acts on it.
        Copies the real bin/local-capable-filter.sh and a small fixture policy/roster into
        the sandbox (setUp's fixture doesn't include either, since most tests never touch
        the filter), then asserts on the OUTCOME -- which alias names are printed -- not on
        whether the flag was accepted.
        """
        shutil.copy2(ROOT / 'bin/local-capable-filter.sh', self.root / 'bin/local-capable-filter.sh')
        (self.root / 'config/remote-agents.sh').write_text(
            'LA_REMOTE_AGENTS=(\n'
            '  "visibleone|gemini|gemini-3.8-flash|Visible One|renewing_free|note"\n'
            '  "hiddenone|nvidia|nvidia/local-capable-model|Hidden One|renewing_free|note"\n'
            ')\n'
        )
        (self.root / 'config/local-capable-remote-models.psv').write_text(
            'nvidia|nvidia/local-capable-model|local-capable|SomeLocalModel|rapid|20|has a local MLX artifact\n'
        )

        default = self.run_cli('--list')
        self.assertIn('visibleone', default.stdout)
        self.assertNotIn('hiddenone', default.stdout,
                         'a model classified local-capable must be absent from --list by default')

        shown = self.run_cli('--local-capable-shown', '--list')
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        self.assertIn('visibleone', shown.stdout)
        self.assertIn('hiddenone', shown.stdout,
                      '--local-capable-shown must make the classified-hidden model appear in --list too, '
                      'not just in the interactive menu')

    def test_csl_owner_returns_via_nav_file_instead_of_exiting(self):
        """`--csl-owner` must hand navigation back through CSL_NAV_FILE, never exit(1).

        REGRESSION GUARD: a direct invocation (no --csl-owner) treats quitting the picker
        with nothing selected as `exit 1` ("nothing selected") -- correct for a standalone
        run. But when csl owns the process (--csl-owner), the SAME quit must write a
        navigation token to CSL_NAV_FILE and exit 0, so csl's own loop can read it and keep
        the process alive instead of csl's shell seeing a failure from its child. This was
        fixed in this branch after a PID mismatch (the writer used its own $$, the reader
        used a DIFFERENT process's $$) made every navigation silently fail to hand off,
        which a bare returncode-only test would not have caught.
        """
        (self.root / 'config/local-capable-remote-models.psv').write_text('# empty, no rows\n')
        shutil.copy2(ROOT / 'bin/local-capable-filter.sh', self.root / 'bin/local-capable-filter.sh')
        navfile = self.root / 'nav-token'
        env = dict(self.env, CSL_NAV_FILE=str(navfile))
        result = subprocess.run(
            ['bash', str(self.root / 'bin/remote-session.sh'), '--csl-owner'],
            input='q\n', env=env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0,
                         '--csl-owner must exit 0 on quit, not treat it as an error: ' + result.stderr)
        self.assertTrue(navfile.exists(), 'quitting under --csl-owner must write CSL_NAV_FILE')
        self.assertEqual(navfile.read_text().strip(), 'quit')
        self.assertNotIn('unknown remote alias', result.stdout + result.stderr,
                         'an empty selection on navigation must never fall through to alias resolution')

        navfile.unlink()
        result = subprocess.run(
            ['bash', str(self.root / 'bin/remote-session.sh'), '--csl-owner'],
            input='h\n', env=env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(navfile.read_text().strip(), 'home',
                         'h) must write "home" to CSL_NAV_FILE, not "quit" or nothing')

    def test_no_bash_scope_errors_on_the_live_launch_path(self):
        """No `local` outside a function anywhere in the script.

        `local claude_cmd=(...)` sat at top level: bash prints "local: can only be used in
        a function" and returns 1, but STILL assigns the array -- so the session launched
        while emitting an error line, and `bash -n` stayed clean. Assert on both the source
        and a real run, because a syntax check cannot see this class of defect.
        """
        source = (self.root / 'bin/remote-session.sh').read_text()
        for lineno, line in enumerate(source.splitlines(), 1):
            self.assertFalse(line.startswith('local '),
                             'top-level `local` at line %d: %s' % (lineno, line))
        result = self.run_cli('--dry-run', 'gemini-flash')
        self.assertNotIn('can only be used in a function', result.stdout + result.stderr)



if __name__ == '__main__':
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
    unittest.main()
