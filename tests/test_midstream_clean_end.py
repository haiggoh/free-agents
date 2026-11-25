#!/usr/bin/env python3
"""tests/test_midstream_clean_end.py — pure unit tests for R3's _midstream_tail (no LiteLLM).

The mid-stream death (NVIDIA NIM buffers tool-call arguments into one final chunk and cuts
~300 s-silent connections) must end the Anthropic stream VALIDLY: close the open block, append
a [free-agents] hint text block, then message_delta(end_turn) + message_stop — never a 500.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import la_proxy_hooks as hooks  # noqa: E402


def _events(tail):
    return [json.loads(b.decode().split("data: ", 1)[1]) for b in tail]


class TailTests(unittest.TestCase):
    def test_open_thinking_block_gets_split_hint(self):
        st = hooks._StreamState(saw_message_start=True, open_block_index=0, next_index=1,
                                last_kind="thinking", saw_thinking=True, output_tokens=42,
                                last_chunk_at=100.0)
        tail = hooks._midstream_tail(st, RuntimeError("https://x/?key=sk-abc boom"), 12.4)
        ev = _events(tail)
        self.assertEqual([e["type"] for e in ev],
                         ["content_block_stop", "content_block_start", "content_block_delta",
                          "content_block_stop", "message_delta", "message_stop"])
        self.assertEqual(ev[0]["index"], 0)
        self.assertEqual(ev[1]["index"], 1)
        self.assertEqual(ev[1]["content_block"], {"type": "text", "text": ""})
        text = ev[2]["delta"]["text"]
        self.assertTrue(text.startswith("[free-agents] The provider closed"), text)
        self.assertIn("RuntimeError", text)
        self.assertIn("12 s", text)
        self.assertIn("write large files in parts", text)   # SPLIT copy
        self.assertEqual(ev[4]["delta"]["stop_reason"], "end_turn")
        self.assertEqual(ev[4]["usage"]["output_tokens"], 42)
        # the reason is the exception CLASS NAME only — never its message (URLs/keys)
        joined = b"".join(tail)
        self.assertNotIn(b"sk-abc", joined)
        self.assertNotIn(b"https://", joined)

    def test_text_then_death_gets_generic_hint_at_next_index(self):
        st = hooks._StreamState(saw_message_start=True, open_block_index=None, next_index=2,
                                last_kind="text", saw_text=True, text_or_tool_after_thinking=True)
        tail = hooks._midstream_tail(st, ValueError("cut"), 3.0)
        ev = _events(tail)
        self.assertEqual([e["type"] for e in ev],
                         ["content_block_start", "content_block_delta", "content_block_stop",
                          "message_delta", "message_stop"])
        self.assertEqual(ev[0]["index"], 2)
        self.assertIn('reply "continue" to resume', ev[1]["delta"]["text"])

    def test_no_message_start_returns_empty(self):
        st = hooks._StreamState()
        self.assertEqual(hooks._midstream_tail(st, RuntimeError("x"), 1.0), [])

    def test_note_parses_bytes_sse_and_dicts(self):
        st = hooks._StreamState(last_chunk_at=0.0)
        hooks._note(b'data: {"type": "message_start", "message": {"usage": {"input_tokens": 7}}}\n\n', st)
        hooks._note({"type": "content_block_start", "index": 0,
                     "content_block": {"type": "thinking"}}, st)
        hooks._note(b'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}}\n\n', st)
        hooks._note(b'garbage\n\n', st)
        self.assertTrue(st.saw_message_start)
        self.assertEqual(st.input_tokens, 7)
        self.assertEqual(st.last_kind, "thinking")
        self.assertEqual(st.open_block_index, 0)
        self.assertEqual(st.next_index, 1)


if __name__ == "__main__":
    unittest.main()
