#!/usr/bin/env python3
"""Contract test (0.59.0, plan Task 3): EVERY launch site that carries a first prompt
must call bin/fa_pre_trust.py BEFORE the claude exec on the same code path.

Static check over each script's text plus one dynamic run per site with a fake claude
that records argv AND the config state at exec time. Planted mutation (removing the
call from one site) turns this red. An empty site list FAILS, never passes.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Phase 0's launch-site table: only sites that accept a first prompt.
# site -> marker of the claude exec line that the helper call must precede
LAUNCH_SITES = {
    "bin/remote-session.sh": '"${claude_cmd[@]}"',
    "bin/launch-local-auto-mode.sh": 'claude --model "$MAIN_SPOOF"',
}

CLAUDE_CAPTURE_STUB = '''#!/usr/bin/env python3
import json, os, sys
if '--help' in sys.argv:
    print('Fixture Claude'); sys.exit(0)
if '--version' in sys.argv:
    print('2.1.286'); sys.exit(0)
with open(os.environ['CLAUDE_ARGV'], 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
state = {"trusted": []}
ccd = os.environ.get("CLAUDE_CONFIG_DIR")
cfg = os.path.join(ccd, ".claude.json") if ccd else os.path.join(os.path.expanduser("~"), ".claude.json")
try:
    with open(cfg) as fh:
        d = json.load(fh)
    state["trusted"] = sorted(k for k, v in d.get("projects", {}).items()
                              if isinstance(v, dict) and v.get("hasTrustDialogAccepted") is True)
except Exception as e:
    state["error"] = e.__class__.__name__
with open(os.environ['CLAUDE_TRUST_STATE'], 'w') as f:
    json.dump(state, f)
'''


class PreTrustContractStatic(unittest.TestCase):
    def test_every_first_prompt_launch_site_pre_trusts_before_exec(self):
        self.assertGreaterEqual(len(LAUNCH_SITES), 2, "empty extraction must FAIL, never pass")
        for rel, exec_marker in LAUNCH_SITES.items():
            with self.subTest(site=rel):
                path = ROOT / rel
                self.assertTrue(path.is_file(), f"{rel}: launch site missing")
                src = path.read_text()
                call_marker = 'fa_pre_trust.py" "$PWD"'  # the call, not a doc mention
                self.assertIn(call_marker, src, f"{rel}: no pre-trust helper call")
                self.assertIn("FA_PRE_TRUST", src, f"{rel}: no FA_PRE_TRUST opt-out")
                self.assertLess(src.index(call_marker), src.index(exec_marker),
                                f"{rel}: helper call must appear before the claude exec")


# --- dynamic run per site -------------------------------------------------------------

_spec = importlib.util.spec_from_file_location("test_remote_session", ROOT / "tests" / "test_remote_session.py")
assert _spec is not None and _spec.loader is not None
trs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trs)


class PreTrustContractDynamicRemote(unittest.TestCase):
    """Dynamic contract for bin/remote-session.sh: run exactly ONE launch test from the
    existing fixture harness (subclassing would re-collect the whole parent class)."""

    def test_launch_with_first_prompt_pre_trusts_before_claude_exec(self):
        case = trs.RemoteSessionTests('test_handover_launch_pre_trusts_before_claude_starts')
        result = unittest.TestResult()
        case.run(result)
        problems = [str(t) + '\n' + str(tb) for t, tb in result.errors + result.failures]
        self.assertEqual(problems, [], 'remote-session.sh launch did not pre-trust:\n' + '\n'.join(problems))


class PreTrustContractDynamicAutoMode(unittest.TestCase):
    """Dynamic contract for bin/launch-local-auto-mode.sh (always carries PROMPT_0)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = Path(self.tmp.name)
        for d in ('bin', 'config', 'stubs', 'home', 'live', 'cfgdir', 'target'):
            (r / d).mkdir()
        for rel in ('bin/launch-local-auto-mode.sh', 'bin/fa_pre_trust.py',
                    'config/config-lib.sh', 'config/emoji.sh'):
            shutil.copy2(ROOT / rel, r / rel)
        (r / 'live' / 'config.local.sh').write_text(
            'la_register testmodel models/testmodel rapid "" "" "" "claude-opus-5" medium\n')
        (r / 'bin' / 'local-llm-hotswap.sh').write_text('#!/bin/sh\necho "SUCCESS_PORT=8000"\n')
        (r / 'bin' / 'local-llm-hotswap.sh').chmod(0o755)

        def stub(name, content):
            p = r / 'stubs' / name
            p.write_text(content)
            p.chmod(0o755)

        stub('curl', '#!/bin/sh\ncat <<\'EOF\'\n{"id":"claude-opus-5"}\nEOF\n')
        stub('lsof', '#!/bin/sh\nif [ "${1:-}" = --help ]; then echo "Fixture: all ports free"; exit 0; fi\nexit 1\n')
        stub('claude', CLAUDE_CAPTURE_STUB)
        (r / 'cfgdir' / '.claude.json').write_text('{"projects": {}}\n')
        self.root = r
        self.env = dict(os.environ, HOME=str(r / 'home'), LA_LIVE_CONFIG_DIR=str(r / 'live'),
                        CLAUDE_CONFIG_DIR=str(r / 'cfgdir'), JOYIA_LOCAL_AUTO_CLASSIFIER='1',
                        PATH=str(r / 'stubs') + ':' + os.environ['PATH'],
                        CLAUDE_ARGV=str(r / 'claude-argv'), CLAUDE_TRUST_STATE=str(r / 'trust-state'))
        self.env.pop('_LA_LIVE_FOR', None)

    def test_auto_mode_launch_pre_trusts_before_claude_exec(self):
        # The launcher's exec-only CLI guard rejects positional args (pre-existing:
        # launch-claude-agent.sh --auto passes them through), so drive it the way the
        # guard itself supports: sourced, with the args as positional params.
        result = subprocess.run(['bash', '-c', 's="$1"; set -- testmodel a; source "$s"', '_',
                                 str(self.root / 'bin/launch-local-auto-mode.sh')],
                                env=self.env, cwd=str(self.root / 'target'),
                                stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertTrue((self.root / 'claude-argv').exists(),
                        'claude was never launched: ' + result.stdout[-1500:] + result.stderr[-1500:])
        state = json.loads((self.root / 'trust-state').read_text())
        self.assertIn(os.path.realpath(str(self.root / 'target')), state['trusted'],
                      'at exec time the helper had not written the trust key')


if __name__ == '__main__':
    unittest.main()
