#!/usr/bin/env python3
"""Doc-lint (plan 2026-11-08 route-watcher Task 5): every `watch-until …` command shown in the
skills must PARSE — run each extracted command with a 1 ms deadline and require an exit code
that is not 2 (usage). Exit 2 = the documented command is broken; 0/1/3/4 = parsed fine
(DETECTED/TIMEOUT/CANNOT_START/STALL). Placeholders (<id>, '<handoff path>', <port>, $TMPDIR)
are substituted with harmless test values first."""
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "watch-until"
SKILLS = [ROOT / "skills" / "free-session-handover" / "SKILL.md",
          ROOT / "skills" / "offload" / "SKILL.md"]


def extract_commands():
    cmds = []
    for f in SKILLS:
        for m in re.finditer(r"`(watch-until --[^`]*)`", f.read_text(encoding="utf-8")):
            cmds.append((f.name, m.group(1)))
    return cmds


def substitute(cmd, tmpdir):
    out = cmd.replace("$TMPDIR", tmpdir)
    for ph, val in (("<session-id>", "doclint-sid"), ("<id>", "doclint-sid"),
                    ("'<handoff path>'", "'handoff.md'"), ("<handoff path>", "handoff.md"),
                    ("<port>", "4399"), ("<alias>", "nvidia-mistral-large4")):
        out = out.replace(ph, val)
    return out


class SkillCommandsParse(unittest.TestCase):
    def test_every_watch_until_command_in_the_skills_parses(self):
        cmds = extract_commands()
        self.assertGreaterEqual(len(cmds), 2,
                                "the skills must show watch-until commands (started + stall)")
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "FA_WATCH_PROJECTS_DIR": tmp}
            for src, cmd in cmds:
                argv = shlex.split(substitute(cmd, tmp))[1:] + ["--deadline", "0.001"]
                r = subprocess.run([sys.executable, str(BIN), *argv],
                                   capture_output=True, text=True, env=env, timeout=20)
                self.assertIn(r.returncode, (0, 1, 3, 4),
                              f"{src}: command does not parse (exit {r.returncode}): "
                              f"{cmd!r}\n{r.stderr}")

    def test_handover_skill_documents_the_stall_swap_proposal(self):
        raw = (ROOT / "skills" / "free-session-handover" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--stall", raw)
        self.assertIn("--proxy-log", raw)
        self.assertIn("--swap-model", raw, "a STALL verdict must lead to proposing --swap-model")
        low = raw.lower()
        self.assertTrue("foreground" in low and "sleep" in low or "polling loop" in low,
                        "the skill must forbid foreground sleeps / polling loops")


if __name__ == "__main__":
    unittest.main()
