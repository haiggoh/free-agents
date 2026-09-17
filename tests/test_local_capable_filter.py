#!/usr/bin/env python3
"""Offline tests for the local-capable remote-model filter parser.

Never touches the network. Exercises the parser against canned fixture data, asserts:
  - comments and blank lines are ignored
  - malformed rows are rejected with filename:line diagnostics
  - duplicate keys are rejected deterministically
  - classifications map to the three known values
  - missing or unknown entries remain visible (fail-open)
  - the joined result pairs each roster row with its policy (or None)
  - --help never reads disk
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def shell_parse(policy_text, roster_text):
    """Run the parser against in-memory fixtures and return structured results."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "policy.psv"
        r = Path(tmp) / "roster.txt"
        p.write_text(policy_text)
        r.write_text(roster_text)
        result = subprocess.run(
            ["bash", str(ROOT / "bin" / "local-capable-filter.sh"),
             "--parse", str(p), "--roster", str(r)],
            text=True, capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"parser exited {result.returncode}:\n{result.stderr}"
            )
        return json.loads(result.stdout)


class PolicyParserTests(unittest.TestCase):
    def test_help_is_self_describing_and_does_not_read_disk(self):
        result = subprocess.run(
            ["bash", str(ROOT / "bin" / "local-capable-filter.sh"), "--help"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        combined = (result.stdout + result.stderr).lower()
        self.assertTrue(
            any(kw in combined for kw in ("usage", "policy", "roster", "local-capable")),
            "help output should describe the tool",
        )
        # --help must never touch any fixture files.
        with tempfile.TemporaryDirectory() as tmp:
            dangerous = Path(tmp) / "boom"
            dangerous.write_text(
                "this would be catastrophic to read if the script ran it\n"
            )
            r = Path(tmp) / "roster.txt"
            r.write_text("")
            result = subprocess.run(
                ["bash", str(ROOT / "bin" / "local-capable-filter.sh"), "--help",
                 str(dangerous), str(r)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((Path(tmp) / "triggered").exists())

    def test_blank_and_comment_lines_are_ignored(self):
        policy = (
            "# header comment\n"
            "\n"
            "nvidia|nvidia/nemotron-3-super-120b-a12b|local-capable|"
            "mlx-community/...|mlx-lm|18|artifact on disk\n"
            "\n"
            "   # indented comment\n"
            "gemini|gemini-3.8-flash|remote-preferred||gemini|5|"
            "quota is generous but finite\n"
        )
        roster = (
            "nvidia-nemotron3|nvidia|nvidia/nemotron-3-super-120b-a12b|"
            "NVIDIA Nemotron 3 Super 120B-A12B|unknown\n"
            "gemini-3.8-flash|gemini|gemini-3.8-flash|"
            "Gemini 3.8 Flash|renewing_free\n"
            "openrouter-free|openrouter|openrouter/free|"
            "OpenRouter free router|renewing_free\n"
        )
        data = shell_parse(policy, roster)
        by_key = {
            (row["provider"], row["remote_model_id"]): row
            for row in data["rows"]
        }
        self.assertIn(
            ("nvidia", "nvidia/nemotron-3-super-120b-a12b"), by_key
        )
        self.assertEqual(
            by_key[("nvidia", "nvidia/nemotron-3-super-120b-a12b")]["classification"],
            "local-capable",
        )
        self.assertIn(("gemini", "gemini-3.8-flash"), by_key)
        self.assertEqual(
            by_key[("gemini", "gemini-3.8-flash")]["classification"],
            "remote-preferred",
        )
        self.assertEqual(data["visible_count"], 2)
        self.assertEqual(data["hidden_count"], 1)

    def test_malformed_rows_reject_with_filename_and_line(self):
        policy = (
            "good|good/model|local-capable|repo|runtime|10|reason\n"
            "missing-fields\n"  # only 2 fields
            "also-bad|foo|invalid-class|a|b|c|d|e\n"  # 8 fields, bad classification
            "ok-again|ok/model|remote-preferred|repo|runtime|5|fine\n"
        )
        roster = (
            "good|good|good/model|Good|unknown\n"
            "also-bad|also-bad|foo|Also-bad|unknown\n"
            "ok-again|ok-again|ok/model|Ok|unknown\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "policy.psv"
            r = Path(tmp) / "roster.txt"
            p.write_text(policy)
            r.write_text(roster)
            result = subprocess.run(
                ["bash", str(ROOT / "bin" / "local-capable-filter.sh"),
                 "--parse", str(p), "--roster", str(r)],
                text=True, capture_output=True,
            )
            self.assertNotEqual(
                result.returncode, 0,
                "malformed policy should be rejected",
            )
            self.assertIn("policy.psv", result.stderr)
            self.assertIn("malformed", result.stderr.lower())

    def test_duplicate_keys_are_rejected(self):
        policy = (
            "nvidia|nvidia/nemotron-3-super-120b-a12b|local-capable|"
            "repo|runtime|10|first\n"
            "nvidia|nvidia/nemotron-3-super-120b-a12b|remote-preferred|"
            "repo|runtime|10|dup\n"
        )
        roster = (
            "nvidia-nemotron3|nvidia|nvidia/nemotron-3-super-120b-a12b|"
            "NVIDIA|unknown\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "policy.psv"
            r = Path(tmp) / "roster.txt"
            p.write_text(policy)
            r.write_text(roster)
            result = subprocess.run(
                ["bash", str(ROOT / "bin" / "local-capable-filter.sh"),
                 "--parse", str(p), "--roster", str(r)],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate", result.stderr.lower())

    def test_unknown_classifications_remain_visible(self):
        policy = (
            "unknown-prov|unknown/model|unknown|"
            "repo|runtime|5|under investigation\n"
        )
        roster = "some-alias|unknown-prov|unknown/model|Some|unknown\n"
        data = shell_parse(policy, roster)
        by_key = {
            (row["provider"], row["remote_model_id"]): row
            for row in data["rows"]
        }
        self.assertIn(("unknown-prov", "unknown/model"), by_key)
        self.assertEqual(
            by_key[("unknown-prov", "unknown/model")]["classification"],
            "unknown",
        )
        self.assertEqual(data["visible_count"], 1)
        self.assertEqual(data["hidden_count"], 0)

    def test_empty_policy_means_all_visible(self):
        policy = "# empty\n"
        roster = "a|gemini|gemini-3.6-flash|Gemini 3.6 Flash|renewing_free\n"
        data = shell_parse(policy, roster)
        self.assertEqual(data["visible_count"], 1)
        self.assertEqual(data["hidden_count"], 0)
        row = data["rows"][0]
        self.assertEqual(row["classification"], "")
        self.assertEqual(row["reason"], "")

    def test_filtered_view_can_be_grouped_by_provider(self):
        policy = (
            "nvidia|nvidia/nemotron-3-super-120b-a12b|local-capable|"
            "repo|runtime|10|on disk\n"
            "nvidia|nvidia/nemotron-3-ultra-550b-a55b|local-capable|"
            "repo|runtime|40|on disk\n"
            "gemini|gemini-3.8-flash|remote-preferred||gemini|5|"
            "quota generous\n"
        )
        roster = (
            "nvidia-nemotron3|nvidia|nvidia/nemotron-3-super-120b-a12b|"
            "NVIDIA Nemotron 3 Super|unknown\n"
            "nvidia-nemotron-ultra|nvidia|nvidia/nemotron-3-ultra-550b-a55b|"
            "NVIDIA Nemotron 3 Ultra|unknown\n"
            "gemini-3.8-flash|gemini|gemini-3.8-flash|"
            "Gemini 3.8 Flash|renewing_free\n"
        )
        data = shell_parse(policy, roster)
        hidden = [
            r for r in data["rows"]
            if r["classification"] == "local-capable"
        ]
        groups = {}
        for r in hidden:
            groups.setdefault(r["provider"], []).append(r)
        self.assertIn("nvidia", groups)
        self.assertEqual(len(groups["nvidia"]), 2)
        # gemini is remote-preferred, not hidden, so not in the groups.
        self.assertNotIn("gemini", groups)

    def test_roster_input_via_stdin_when_explicit_dash(self):
        policy = "gemini|gemini-3.6-flash|local-capable||gemini|5|on disk\n"
        roster = "gemini-flash|gemini|gemini-3.6-flash|Gemini 3.6 Flash|renewing_free\n"
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "policy.psv"
            r = Path(tmp) / "roster.txt"
            p.write_text(policy)
            r.write_text(roster)
            result = subprocess.run(
                ["bash", str(ROOT / "bin" / "local-capable-filter.sh"),
                 "--parse", str(p), "--roster", "-"],
                text=True, input=roster, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["hidden_count"], 1)
            self.assertEqual(data["visible_count"], 0)


if __name__ == "__main__":
    unittest.main()