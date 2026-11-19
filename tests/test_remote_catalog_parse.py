#!/usr/bin/env python3
"""tests/test_remote_catalog_parse.py — 0.59.9 pin (user report 2026-11-19: "only 7 nvidia
models in the remote session list … I want to make sure the list is fetched correctly").

The fetch itself was verified live (integrate.api.nvidia.com/v1/models returns the complete
catalog — 32 rows, anon == authed, no pagination — on 2026-11-19; the "85 models" of 2026-10-31
is upstream shrinkage, not a fetch bug). What these tests pin is the PARSE layer: a correct
fetch must not silently lose rows. candidates() keeps every chat/tool row and drops only the
documented non-candidates; empty/malformed/error/no-candidate bodies exit 1 loudly (the picker
shows an error row instead of a silently empty group).

Usage: python3 tests/test_remote_catalog_parse.py [-v]   (or pytest)
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSE = ROOT / 'bin' / 'remote-catalog-parse.py'


def run_parse(payload_text):
    return subprocess.run([sys.executable, str(PARSE)], input=payload_text,
                          text=True, capture_output=True)


class CatalogParseTests(unittest.TestCase):
    def test_nvidia_shaped_payload_keeps_every_chat_row(self):
        """A live-shaped NVIDIA body ({"object": "list", "data": [...]}) with N chat rows and one
        non-chat row must yield exactly N candidate rows — no silent drops."""
        rows = [{"id": f"org/model-{i}", "type": "chat"} for i in range(31)]
        rows.append({"id": "org/vlm-1", "type": "vlm"})          # non-chat: documented drop
        r = run_parse(json.dumps({"object": "list", "data": rows}))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = [ln for ln in r.stdout.splitlines() if ln]
        self.assertEqual(len(out), 31, 'every chat row must survive the parse')
        self.assertNotIn('org/vlm-1', r.stdout)

    def test_drops_only_documented_non_candidates(self):
        rows = [
            {"id": "good/chat-1", "type": "chat"},
            {"id": "bad/notools", "type": "chat", "tools_calling": False},
            {"id": "bad/nochatchat", "type": "chat", "capabilities": {"completion_chat": False}},
            {"id": "bad/nofunc", "type": "chat", "capabilities": {"function_calling": False}},
            {"id": "~weird/alias"},                                # fails the id pattern
            "not-a-dict",
            {"type": "chat"},                                      # no id
        ]
        r = run_parse(json.dumps({"data": rows}))
        self.assertEqual(r.returncode, 0, r.stderr)
        ids = [ln.split('\t')[0] for ln in r.stdout.splitlines() if ln]
        self.assertEqual(ids, ["good/chat-1"])

    def test_models_prefix_is_stripped(self):
        r = run_parse(json.dumps({"data": [{"id": "models/gemini-x", "type": "chat"}]}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("gemini-x\t"), r.stdout)

    def test_empty_error_and_no_candidate_bodies_fail_loudly(self):
        bodies = [
            '',
            'not json',
            '{"data": []}',
            '{"success": false, "data": [{"id": "x", "type": "chat"}]}',
            '{"data": [{"id": "x", "capabilities": {"completion_chat": False}}]}',
            '{"other": 1}',
        ]
        for body in bodies:
            r = run_parse(body)
            self.assertEqual(r.returncode, 1, f'body must fail loudly: {body!r}')
            self.assertIn('no chat/tool candidates', r.stderr)

    def test_help_runs_without_reading_stdin(self):
        r = subprocess.run([sys.executable, str(PARSE), '--help'],
                           input='', text=True, capture_output=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn('remote-catalog-parse.py', r.stdout)


if __name__ == '__main__':
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
    unittest.main()
