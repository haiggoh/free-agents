#!/usr/bin/env python3
"""Contract test: every flag remote-session.sh adds to claude_cmd must exist in the INSTALLED
claude CLI's top-level --help (waypoint fa-claude-flag-contract-test).

0.56.8 shipped `--trust-workspace` unconditionally; CC 2.1.322 only has it on the
`ultrareview` subcommand, so top-level `claude` rejected it and EVERY free launch died at
startup. 0.56.12 gated that one flag; this test generalises the gate to every statically
added flag, and it is collected by the main rtc-ship gate (pytest tests/ …), so a release
cannot ship a flag the CLI rejects. User passthrough flags (PASSTHRU) are not static and are
out of scope.

The extractor and the help-parser are pure functions pinned by unit tests with a planted
bogus flag, so the test cannot pass vacuously on an empty extraction.
"""
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "remote-session.sh"


def extract_claude_cmd_flags(script_text: str) -> tuple:
    """(ungated, gated) long flags statically appended to claude_cmd in remote-session.sh.

    A flag is GATED when its append sits inside an `if grep … _claude_help` block — the
    0.56.12 runtime contract that only passes a flag the installed CLI lists (that is how
    --trust-workspace is shipped: present in the script, conditional at runtime). Ungated
    flags are passed unconditionally and must ALL be top-level options of `claude --help`.
    """
    lines = script_text.splitlines()
    ungated, gated = set(), set()
    for i, line in enumerate(lines):
        if "claude_cmd+=" not in line:
            continue
        toks = re.findall(r"--[a-z][a-z0-9-]*", line)
        if not toks:
            continue
        # walk back at most 3 lines for the help-grep guard opening the enclosing if
        # (a wider window misclassifies later appends like the --plugin-dir line, whose own
        # guard `[ -n "${LA_MOD_DIR:-}" ] &&` sits 4 lines under the --trust-workspace if)
        guarded = any("_claude_help" in lines[j] and "grep" in lines[j]
                      for j in range(max(0, i - 3), i))
        (gated if guarded else ungated).update(toks)
    return ungated, gated


def help_options(help_text: str) -> set:
    """Top-level option lines of `claude --help`: an indented `--flag` (optionally with a
    short form first). Same shape remote-session.sh's 0.56.12 gate greps for."""
    return set(re.findall(r"^[ \t]+(?:-[a-zA-Z], )?(--[a-z][a-z0-9-]*)", help_text, re.M))


class ExtractorTests(unittest.TestCase):
    def test_extracts_all_static_flags(self):
        text = 'claude_cmd+=(--permission-mode "$PERMISSION_MODE")\n' \
               'claude_cmd+=(--effort "$EFFORT_CHOICE")\n' \
               'claude_cmd+=("$(<"$PROMPT_FILE")")\n'   # no flag, must be ignored
        ungated, gated = extract_claude_cmd_flags(text)
        self.assertEqual(ungated, {"--permission-mode", "--effort"})
        self.assertEqual(gated, set())

    def test_help_gated_flag_is_not_required_in_help(self):
        # 0.56.12 shape: the append sits under a grep of the CLI's own --help, so the flag
        # may be absent from top-level help (it is only passed when listed)
        text = '_claude_help=$(claude --help)\n' \
               'if grep -qE "--trust-workspace" <<<"$_claude_help"; then\n' \
               '    claude_cmd+=(--trust-workspace)\nfi\n'
        ungated, gated = extract_claude_cmd_flags(text)
        self.assertEqual(ungated, set())
        self.assertEqual(gated, {"--trust-workspace"})

    def test_planted_bogus_flag_is_detected(self):
        # mutation control: a flag the CLI does not offer must land in the missing set
        script = 'claude_cmd+=(--bogus-flag-xyz)\n'
        help_text = "Usage: claude [options]\n\n  --permission-mode <mode>\n"
        ungated, _ = extract_claude_cmd_flags(script)
        missing = ungated - help_options(help_text)
        self.assertEqual(missing, {"--bogus-flag-xyz"})

    def test_empty_extraction_is_empty_not_error(self):
        self.assertEqual(extract_claude_cmd_flags("echo hi\n"), (set(), set()))


class FlagContractTests(unittest.TestCase):
    def test_every_claude_cmd_flag_exists_in_installed_help(self):
        ungated, gated = extract_claude_cmd_flags(SCRIPT.read_text())
        # an empty extraction is a FAILURE, never a pass (the script must really build the cmd)
        self.assertGreaterEqual(len(ungated), 4,
                                f"only {len(ungated)} ungated flags extracted from remote-session.sh")
        print(f"\nclaude_cmd flags: {len(ungated)} ungated, {len(gated)} help-gated {sorted(gated)}")
        claude = shutil.which("claude")
        if claude is None:
            # loud skip: the contract is NOT checked on a machine without the CLI
            print("\n⚠ SKIP-LOUD: no `claude` on PATH — the claude_cmd flag contract was NOT "
                  "verified against the installed CLI", file=sys.stderr)
            self.skipTest("claude CLI not installed — flag contract not checked")
        r = subprocess.run([claude, "--help"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        opts = help_options(r.stdout)
        self.assertGreaterEqual(len(opts), 10, "claude --help parsed to suspiciously few options")
        missing = ungated - opts
        self.assertEqual(missing, set(),
                         f"remote-session.sh passes flags UNGATED that the installed claude "
                         f"--help does not list (would crash every launch like 0.56.8's "
                         f"--trust-workspace): {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
