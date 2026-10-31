#!/usr/bin/env python3
"""Every skills/*/SKILL.md body stays within check-code's SKILL-BUDGET (1500 words) —
long-form material belongs in references/. Pins the invariant repo-wide.

Usage: python3 tests/test_skill_budget.py [-v]
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGET = 1500  # check-code DEFAULT_SKILL_BUDGET_WORDS


def body_words(path: Path) -> int:
    t = path.read_text(encoding="utf-8")
    body = t.split("---", 2)[2] if t.startswith("---") else t
    return len(re.findall(r"\S+", body))


class SkillBudgetTests(unittest.TestCase):
    def test_all_skill_files_within_budget(self):
        skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
        self.assertTrue(skills, "no skills found")
        over = {str(p.relative_to(ROOT)): body_words(p) for p in skills if body_words(p) > BUDGET}
        self.assertEqual(over, {}, f"SKILL-BUDGET exceeded (budget {BUDGET}); move detail to references/")

    def test_no_orphan_offload_to_local_dir(self):
        # 0.49.8: the offload-to-local -> offload rename (0.49.7) must not leave the old
        # dir behind; its in-depth content is merged into skills/offload/references/.
        self.assertFalse((ROOT / "skills" / "offload-to-local").exists(),
                         "orphaned skills/offload-to-local/ — its content belongs in "
                         "skills/offload/references/offload-in-depth.md")


if __name__ == "__main__":
    unittest.main()
