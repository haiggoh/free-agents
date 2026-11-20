#!/usr/bin/env python3
"""0.59.9: both free-lane session system prompts carry the tool-casing line
(the shell tool is `Bash` capital B, never `bash`) so free-lane models see it in
the narrow channel they actually read. 2026-11-19: recurred on free lanes despite
CLAUDE.md, which the WYS primer now also owns (watch-your-step 0.10.4)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = [ROOT / "config" / "local-agent-system-prompt.txt",
           ROOT / "config" / "remote-agent-system-prompt.txt"]
LINE = "CASE-SENSITIVE"


class PromptCasingTests(unittest.TestCase):
    def test_both_prompts_carry_the_casing_line(self):
        for p in PROMPTS:
            text = p.read_text(encoding="utf-8")
            self.assertIn(LINE, text, p.name)
            self.assertIn("`Bash`", text, p.name)
            self.assertIn("never `bash`", text, p.name)

    def test_line_is_inside_the_schema_reminder_block(self):
        text = PROMPTS[0].read_text(encoding="utf-8")
        schema = text.find("TOOL SCHEMA REMINDER")
        line = text.find(LINE)
        self.assertGreaterEqual(schema, 0)
        self.assertGreater(line, schema)


if __name__ == "__main__":
    unittest.main()
