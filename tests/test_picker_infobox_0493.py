#!/usr/bin/env python3
"""tests/test_picker_infobox_0493.py — 0.49.7 (H4): the remote infobox must not repeat the
model's name on lines 1 and 2 (Le Chonk's note started with the display name; user 2026-10-31).
The test asserts the name appears EXACTLY ONCE. Mutation: strip_leading_name returning the
note unchanged makes test_le_chonk_name_appears_once fail (count 2)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_picker_model as m  # noqa: E402


class InfoboxDedupeTests(unittest.TestCase):
    def _screen(self, agent):
        return m.RemoteScreen(m.Settings(), [agent])

    def test_le_chonk_name_appears_once_in_infobox(self):
        agent = m.RemoteAgent("nvidia-mistral-large4", "nvidia", "Mistral Large 4 (Le Chonk)",
                              "unknown", model_id="mistralai/mistral-large-4-1t-a52b")
        text = self._screen(agent).info_text("nvidia-mistral-large4")
        self.assertEqual(text.count("Mistral Large 4 (Le Chonk)"), 1,
                         f"name must appear once, not echoed by the note:\n{text}")
        self.assertIn("TOP PICK", text, "the note's content must survive the dedupe")

    def test_strip_leading_name_unit(self):
        self.assertEqual(m.strip_leading_name("Mistral Large 4 (Le Chonk)",
                                              "Mistral Large 4 (Le Chonk) — TOP PICK"),
                         "TOP PICK")
        self.assertEqual(m.strip_leading_name("X", "A different note"), "A different note")
        self.assertEqual(m.strip_leading_name("", "note"), "note")

    def test_repetition_is_stripped_but_note_content_kept(self):
        # Step 5's note also starts with its display name — the dedupe must strip the echo
        # while keeping the note's own content, and the name must then appear exactly once.
        agent = m.RemoteAgent("nvidia-step5-preview", "nvidia", "StepFun Step 5 Preview",
                              "unknown", model_id="stepfun-ai/step-5-preview")
        text = self._screen(agent).info_text("nvidia-step5-preview")
        note = m.model_note("nvidia-step5-preview")
        self.assertTrue(note)
        rest = m.strip_leading_name("StepFun Step 5 Preview", note)
        self.assertNotEqual(rest, note, "this note does repeat the name — the test needs such a note")
        self.assertIn(rest[:40], text, "the note's content after the stripped name must survive")
        self.assertEqual(text.count("StepFun Step 5 Preview"), 1)

    def test_step5_infobox_phrasing_is_fastest_not_best(self):
        # user 2026-11-10: step 5 infobox reworded (config/remote-model-notes.json) to
        # "Currently the fastest model on NVIDIA: ..." plus the preview-stuck caveat,
        # replacing "one of the best models on NVIDIA". Mutation: reverting the note
        # makes this fail (old phrase / missing caveat rendered again).
        agent = m.RemoteAgent("nvidia-step5-preview", "nvidia", "StepFun Step 5 Preview",
                              "unknown", model_id="stepfun-ai/step-5-preview")
        text = self._screen(agent).info_text("nvidia-step5-preview")
        self.assertIn("Currently the fastest model on NVIDIA", text,
                      f"new phrasing must render in the infobox:\n{text}")
        self.assertIn("still tends to get stuck on difficult problems", text,
                      f"preview caveat must render in the infobox:\n{text}")
        self.assertNotIn("one of the best", text.lower(),
                         f"old phrasing must be gone from the infobox:\n{text}")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
    unittest.main()
