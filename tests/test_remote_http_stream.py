#!/usr/bin/env python3
"""bin/remote_http._consume_stream: SSE framing the providers really send.

Usage: python3 tests/test_remote_http_stream.py [-v]
Environment: none required; responses are in-memory fakes, nothing is sent.
"""
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import remote_http as rh  # noqa: E402


class _Resp:
    def __init__(self, lines):
        self.lines = [ln.encode() for ln in lines]

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def __iter__(self):
        while self.lines:
            yield self.lines.pop(0)

    def read(self):
        rest, self.lines = b"".join(self.lines), []
        return rest


def ev(delta, **extra):
    return "data: " + json.dumps({"choices": [{"delta": delta}], **extra}) + "\n"


def run(lines):
    out, rsn = io.StringIO(), io.StringIO()
    res = rh._consume_stream(_Resp(lines), out, rsn)
    return res, out.getvalue(), rsn.getvalue()


class StreamFramingTests(unittest.TestCase):
    def test_plain_stream(self):
        res, out, _ = run([ev({"content": "A"}), "\n", ev({"content": "B"}), "data: [DONE]\n"])
        self.assertEqual((out, res["complete"]), ("AB", True))

    def test_keepalive_comment_before_first_event(self):
        # SSE comments (": ping") and blank lines are legal before the first event; a slow
        # first token (GLM 5.3's warm-up) is exactly when servers send them.
        res, out, _ = run([": ping\n", "\n", ev({"content": "A"}), "data: [DONE]\n"])
        self.assertEqual((out, res["complete"]), ("A", True))

    def test_blank_first_line(self):
        res, out, _ = run(["\n", ev({"content": "A"}), "data: [DONE]\n"])
        self.assertEqual((out, res["complete"]), ("A", True))

    def test_event_and_id_fields(self):
        res, out, _ = run(["event: message\n", "id: 1\n", ev({"content": "A"}), "data: [DONE]\n"])
        self.assertEqual((out, res["complete"]), ("A", True))

    def test_reasoning_goes_to_its_own_file(self):
        res, out, rsn = run([ev({"reasoning_content": "think"}), ev({"content": "A"}),
                             "data: [DONE]\n"])
        self.assertEqual((out, rsn, res["rchars"]), ("A", "think", 5))

    def test_plain_json_body_still_parsed(self):
        body = json.dumps({"model": "m", "choices": [{"message": {"content": "hi"}}]})
        res, out, _ = run([body + "\n"])
        self.assertEqual((out, res["complete"]), ("hi", True))


if __name__ == "__main__":
    unittest.main()
